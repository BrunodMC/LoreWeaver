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
    assert "proposes answers to open threads" in page and f'href="/arc/{world["todo"]}"' in page
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
    assert "proposes answers to open threads" not in page  # the new version touched none

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
