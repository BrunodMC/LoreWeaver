from contextlib import closing

import pytest

from src.app import db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


@pytest.fixture
def world(client):
    with closing(db.get_connection()) as conn:
        ids = {
            "mara": db.create_entity(conn, "npc", "Mara Vell", {}, known=True),
            "guild": db.create_entity(conn, "faction", "Ferrymen's Guild", {}),
            "todo": db.create_entity(conn, "arc", "Open threads", {"notes": "Who sank the barge?"}, status="draft"),
        }
        db.set_ai_context(conn, ids["todo"], True)
    return ids


def script(*responses):
    llm.set_backend(llm.FakeBackend(list(responses)))


def answer(world, **overrides):
    data = {
        "concept": "A ferry pilot who knows too much.",
        "name": "Ossa Grell",
        "fields": {"personality": "Wry.", "hook": "Can smuggle anyone."},
        "proposed_links": [
            {"target_id": world["guild"], "direction": "from_new", "label": "works for", "party_knows": True,
             "why": "pays her"},
            {"target_id": world["mara"], "direction": "to_new", "label": "owes money to", "party_knows": False,
             "why": "old debt"},
        ],
        "open_threads_touched": [{"entry_id": world["todo"], "thread": "Who sank the barge?", "proposal": "Ossa did."}],
        "dm_notes": "Use her at the docks.",
    }
    data.update(overrides)
    return data


@pytest.fixture
def draft_id(world):
    script(answer(world))
    return generation.start(generation.GenerationRequest("npc", "a ferry pilot", [world["guild"]])).entity_id


def entity(entity_id):
    with closing(db.get_connection()) as conn:
        return db.get_entity(conn, entity_id)


def versions(entity_id):
    with closing(db.get_connection()) as conn:
        return db.lineage_versions(conn, db.lineage_for_entity(conn, entity_id))


def test_ai_draft_shows_the_review_panel_instead_of_plain_promotion(client, world, draft_id):
    page = client.get(f"/npc/{draft_id}").text
    assert "AI draft under review" in page
    assert "Brief:</span> a ferry pilot" in page and "A ferry pilot who knows too much." in page
    assert "Decisions for you" in page and f'href="/arc/{world["todo"]}"' in page
    assert f'href="/faction/{world["guild"]}"' in page and "works for" in page
    assert ">Promote to canon</button>" in page
    assert '"status": "canon"' not in page  # the plain status button is gone
    assert "1 / 1" in page and "in place now" in page
    status = client.post(f"/npc/{draft_id}/known", data={"known": "1"}).text
    assert '"status": "canon"' not in status  # still gone after an HTMX status swap


def test_hand_made_drafts_keep_plain_promotion(client, world):
    page = client.get(f"/arc/{world['todo']}").text
    assert "AI draft under review" not in page and '"status": "canon"' in page
    assert client.post(f"/arc/{world['todo']}/promote").status_code == 404


def test_promote_creates_only_the_ticked_links(client, world, draft_id):
    response = client.post(f"/npc/{draft_id}/promote", data={"link": ["1"]}, follow_redirects=False)
    assert response.status_code == 303
    promoted = entity(draft_id)
    assert promoted["status"] == "canon" and promoted["known_to_party"] == 0
    with closing(db.get_connection()) as conn:
        links = db.links_for(conn, draft_id)
    assert links["outgoing"] == []
    assert [(l["other_name"], l["relation_label"], l["known_to_party"]) for l in links["incoming"]] == [
        ("Mara Vell", "owes money to", 0)]
    assert "AI draft under review" not in client.get(f"/npc/{draft_id}").text
    assert client.post(f"/npc/{draft_id}/promote").status_code == 409


def test_promote_skips_links_to_entries_deleted_since(client, world, draft_id):
    with closing(db.get_connection()) as conn:
        db.delete_entity(conn, world["guild"])
    client.post(f"/npc/{draft_id}/promote", data={"link": ["0", "1"]})
    with closing(db.get_connection()) as conn:
        assert len(db.links_for(conn, draft_id)["incoming"]) == 1
    assert entity(draft_id)["status"] == "canon"


def test_discard_deletes_the_draft_but_keeps_the_log(client, world, draft_id):
    response = client.post(f"/npc/{draft_id}/discard", follow_redirects=False)
    assert response.headers["location"] == "/npc?status=draft"
    assert entity(draft_id) is None
    with closing(db.get_connection()) as conn:
        row = conn.execute("SELECT entity_id, output FROM generations").fetchone()
    assert row[0] is None and "Ossa Grell" in row[1]


def test_regenerate_versions_and_restore(client, world, draft_id):
    with closing(db.get_connection()) as conn:
        db.update_entity(conn, draft_id, "Ossa Grell", {**entity(draft_id)["fields"], "personality": "My edit."})
    script({"concept": "Darker.", "fields": {"hook": "Owes the drowned."}, "dm_notes": "Darker.",
            "proposed_links": [{"target_id": world["guild"], "direction": "from_new", "label": "works for",
                                "party_knows": False, "why": "still"}],
            "open_threads_touched": []})
    response = client.post(f"/npc/{draft_id}/regenerate", data={"notes": "darker hook", "deep": "1"},
                           follow_redirects=False)
    assert client.get(response.headers["location"], follow_redirects=False).headers["location"] == f"/npc/{draft_id}"

    history = versions(draft_id)
    assert [v["kind"] for v in history] == ["generated", "edit", "generated"]
    assert history[2]["notes"] == "darker hook" and history[2]["brief"] == "a ferry pilot"
    assert history[2]["anchors"] == [world["guild"]]
    now = entity(draft_id)["fields"]
    assert now["personality"] == "My edit." and now["hook"] == "Owes the drowned."

    page = client.get(f"/npc/{draft_id}").text
    assert "3 / 3" in page and "darker hook" in page
    assert "Decisions for you" not in page  # the new version touched none

    first = client.get(f"/npc/{draft_id}/versions/{history[0]['id']}").text
    assert "1 / 3" in first and "Use this version" in first and "AI attempt" in first
    edit = client.get(f"/npc/{draft_id}/versions/{history[1]['id']}").text
    assert "your edit" in edit and 'class="changed"' in edit

    client.post(f"/npc/{draft_id}/versions/{history[0]['id']}/restore")
    assert entity(draft_id)["fields"]["personality"] == "Wry."
    assert len(versions(draft_id)) == 3  # the state replaced was already in the history
    assert "1 / 3" in client.get(f"/npc/{draft_id}").text and "in place now" in client.get(f"/npc/{draft_id}").text


def test_review_routes_are_dm_only_and_check_their_targets(client, world, draft_id):
    assert client.get(f"/npc/{draft_id}/versions/999").status_code == 404
    assert client.post(f"/faction/{draft_id}/promote").status_code == 404  # wrong type
    client.get("/view/party")
    assert client.post(f"/npc/{draft_id}/promote").status_code == 404
    assert client.post(f"/npc/{draft_id}/discard").status_code == 404


def test_inbox_lists_ai_drafts_awaiting_review(client, world, draft_id):
    page = client.get("/drafts").text
    assert f'href="/npc/{draft_id}"' in page and "Ossa Grell" in page
    assert "Brief: a ferry pilot" in page and "A ferry pilot who knows too much." in page
    assert "1 decision" in page
    assert "Open threads" not in page  # hand-made drafts aren't AI drafts under review

    client.post(f"/npc/{draft_id}/promote")
    assert "Ossa Grell" not in client.get("/drafts").text
    assert "The loom is idle" in client.get("/drafts").text


def test_inbox_shows_running_generations(client, world, monkeypatch):
    job = generation.Job(id="abc123", request=generation.GenerationRequest("adversary", "river wyrm"))
    monkeypatch.setitem(generation.JOBS, job.id, job)
    page = client.get("/drafts").text
    assert "Weaving now" in page and 'href="/generate/jobs/abc123"' in page and "river wyrm" in page


def test_inbox_is_dm_only(client):
    client.get("/view/party")
    assert client.get("/drafts").status_code == 404


def test_panel_shows_claudes_notes_and_promote_leaves_them_behind(client, world, draft_id):
    page = client.get(f"/npc/{draft_id}").text
    assert "Claude&#39;s notes" in page or "Claude's notes" in page
    assert "Use her at the docks." in page and 'name="keep_notes"' in page
    assert "notes" not in entity(draft_id)["fields"]

    client.post(f"/npc/{draft_id}/promote")
    assert "notes" not in entity(draft_id)["fields"]
    page = client.get(f"/npc/{draft_id}").text
    assert f'href="/npc/{draft_id}/history"' in page and "Use her at the docks." not in page


def test_promote_can_keep_claudes_notes_after_the_gms_own(client, world, draft_id):
    with closing(db.get_connection()) as conn:
        db.update_entity(conn, draft_id, "Ossa Grell", {**entity(draft_id)["fields"], "notes": "Mine first."})
    client.post(f"/npc/{draft_id}/promote", data={"keep_notes": "1"})
    assert entity(draft_id)["fields"]["notes"] == "Mine first.\n\n||Use her at the docks.||"


def test_drafting_history_stays_readable_after_promotion(client, world, draft_id):
    client.post(f"/npc/{draft_id}/promote")
    history = client.get(f"/npc/{draft_id}/history").text
    assert "Drafting history" in history and "Brief:</span> a ferry pilot" in history
    assert "Use her at the docks." in history and "Use this version" not in history
    first = versions(draft_id)[0]["id"]
    assert client.get(f"/npc/{draft_id}/versions/{first}").status_code == 200
    assert client.post(f"/npc/{draft_id}/versions/{first}/restore").status_code == 409
    assert client.get(f"/npc/{world['mara']}/history").status_code == 404  # never generated
    client.get("/view/party")
    assert client.get(f"/npc/{draft_id}/history").status_code == 404


def test_restore_keeps_the_gms_current_notes(client, world, draft_id):
    script({"concept": "c", "fields": {"hook": "New hook."}, "proposed_links": [],
            "open_threads_touched": [], "dm_notes": "n"})
    client.post(f"/npc/{draft_id}/regenerate", data={"notes": "new hook"})
    with closing(db.get_connection()) as conn:
        db.update_entity(conn, draft_id, "Ossa Grell", {**entity(draft_id)["fields"], "notes": "Written later."})
    client.post(f"/npc/{draft_id}/versions/{versions(draft_id)[0]['id']}/restore")
    fields = entity(draft_id)["fields"]
    assert fields["hook"] == "Can smuggle anyone." and fields["notes"] == "Written later."


def test_legacy_notes_move_out_of_entries_but_gm_writing_stays(client, world):
    legacy = {"personality": "Wry.", "notes": "||Old AI notes.||"}
    with closing(db.get_connection()) as conn:
        old = db.create_entity(conn, "npc", "Old Draft", legacy, status="draft")
        lineage = db.record_generation(conn, entity_type="npc", kind="generated", entity_id=old,
                                       output={"name": "Old Draft", "fields": dict(legacy), "dm_notes": "Old AI notes."})
        kept = db.create_entity(conn, "npc", "Edited Draft", {"notes": "||Old AI notes.|| Plus mine."}, status="draft")
        db.record_generation(conn, entity_type="npc", kind="generated", entity_id=kept,
                             output={"name": "Edited Draft", "fields": {}, "dm_notes": "Old AI notes."})

        assert generation.move_legacy_notes(conn) == 1
        assert generation.move_legacy_notes(conn) == 0  # idempotent
        assert db.get_entity(conn, old)["fields"] == {"personality": "Wry."}
        assert db.get_generation(conn, lineage)["output"]["fields"] == {"personality": "Wry."}
        assert db.get_entity(conn, kept)["fields"]["notes"] == "||Old AI notes.|| Plus mine."
        # the version in place still matches the entry, so the panel doesn't see a phantom edit
        assert generation.review_state(conn, db.get_entity(conn, old))["current"]["id"] == lineage


def implicating(world, **overrides):
    return answer(world, canon_implications=[
        {"entry_id": world["mara"], "implication": "Mara once ||secretly|| ran the ferry herself."}], **overrides)


def test_decisions_list_threads_and_implications(client, world):
    script(implicating(world))
    draft = generation.start(generation.GenerationRequest("npc", "a ferry pilot", [world["guild"]])).entity_id
    page = client.get(f"/npc/{draft}").text
    assert "open thread" in page and "Ossa did." in page
    assert f'href="/npc/{world["mara"]}"' in page and "Mara once secretly ran the ferry herself." in page
    assert page.count("✓ Accept") == 2 and "2 decisions" in client.get("/drafts").text


def test_accept_notes_the_claim_on_its_entry_once_and_without_a_date(client, world):
    script(implicating(world))
    draft = generation.start(generation.GenerationRequest("npc", "a ferry pilot", [world["guild"]])).entity_id
    with closing(db.get_connection()) as conn:
        db.update_entity(conn, world["mara"], "Mara Vell", {"notes": "My own note."})
    client.post(f"/npc/{draft}/decisions/c0/accept")
    client.post(f"/npc/{draft}/decisions/c0/accept")  # a double click adds nothing
    client.post(f"/npc/{draft}/decisions/t0/accept")

    assert entity(world["mara"])["fields"]["notes"] == (
        "My own note.\n\n||From Ossa Grell: Mara once secretly ran the ferry herself.||")
    assert entity(world["todo"])["fields"]["notes"] == (
        "Who sank the barge?\n\n||From Ossa Grell, on “Who sank the barge?”: Ossa did.||")
    page = client.get(f"/npc/{draft}").text
    assert page.count("accepted · noted on that entry") == 2 and "✓ Accept" not in page
    assert "decision" not in client.get("/drafts").text.split("Ossa Grell")[1].split("</li>")[0]


def test_reject_prefills_the_next_regeneration(client, world, draft_id):
    client.post(f"/npc/{draft_id}/decisions/t0/reject")
    page = client.get(f"/npc/{draft_id}").text
    assert ">Drop this: Ossa did.</textarea>" in page and "rejections included" in page
    assert "Ossa did." not in entity(world["todo"])["fields"]["notes"]


def test_bad_decisions_are_404_and_only_drafts_decide(client, world, draft_id):
    assert client.post(f"/npc/{draft_id}/decisions/c9/accept").status_code == 404
    assert client.post(f"/npc/{draft_id}/decisions/t0/shrug").status_code == 404
    client.post(f"/npc/{draft_id}/promote")
    assert client.post(f"/npc/{draft_id}/decisions/t0/accept").status_code == 409
