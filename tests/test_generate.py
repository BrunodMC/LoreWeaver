from contextlib import closing

import pytest

from src.app import db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


@pytest.fixture
def world(client):
    """client first, so the app has created the schema."""
    with closing(db.get_connection()) as conn:
        ids = {
            "mara": db.create_entity(conn, "npc", "Mara Vell", {"hook": "Lost ledger."}, known=True),
            "guild": db.create_entity(conn, "faction", "Ferrymen's Guild", {}),
            "todo": db.create_entity(conn, "arc", "Open threads", {"notes": "Who sank the barge?"}, status="draft"),
            "hidden": db.create_entity(conn, "location", "Unflagged draft", {}, status="draft"),
        }
        db.set_ai_context(conn, ids["todo"], True)
    return ids


def script(*responses):
    fake = llm.FakeBackend(list(responses))
    llm.set_backend(fake)
    return fake


def answer(**overrides):
    data = {
        "concept": "A ferry pilot who knows too much.",
        "name": "Ossa Grell",
        "fields": {"ancestry_role": "Human · Ferry pilot", "personality": "Wry. ||Paid by the guild.||",
                   "voice": "Short sentences; laughs at her own jokes."},
        "proposed_links": [],
        "open_threads_touched": [],
        "dm_notes": "Use her at the docks.",
    }
    data.update(overrides)
    return data


def link_to(target_id, label="works for"):
    return {"target_id": target_id, "direction": "from_new", "label": label, "party_knows": False, "why": "debt"}


def entity(entity_id):
    with closing(db.get_connection()) as conn:
        return db.get_entity(conn, entity_id)


def submit(client, **data):
    return client.post("/generate", data=data, follow_redirects=False)


def test_form_preanchors_only_entries_the_ai_can_see(client, world):
    page = client.get(f"/generate?anchor={world['mara']}&anchor={world['hidden']}&type=adversary").text
    assert f'name="anchor" value="{world["mara"]}"' in page
    assert f'value="{world["hidden"]}"' not in page
    assert '<option value="adversary" selected>' in page
    assert '<option value="pc"' not in page and '<option value="session"' not in page


def test_generation_is_dm_only(client, world):
    client.get("/view/party")
    assert client.get("/generate").status_code == 404
    assert submit(client, type="npc", brief="x").status_code == 404
    assert "Generate" not in client.get("/").text.split("<main>")[0]


def test_anchor_search_and_chips_cover_the_ai_context_only(client, world):
    results = client.get("/generate/anchors", params={"q": "e"}).text  # every name has an "e"
    for entry in ("guild", "todo", "mara"):
        assert f"/generate/anchor-chip/{world[entry]}" in results
    assert f"/generate/anchor-chip/{world['hidden']}" not in results
    assert "undecided" in results
    assert "Nothing in the AI context" in client.get("/generate/anchors", params={"q": "zzz"}).text
    chip = client.get(f"/generate/anchor-chip/{world['guild']}")
    assert f'name="anchor" value="{world["guild"]}"' in chip.text
    assert client.get(f"/generate/anchor-chip/{world['hidden']}").status_code == 404


@pytest.mark.parametrize("data, message", [
    ({"type": "npc", "brief": ""}, "Write a brief, pick an anchor"),
    ({"type": "pc", "brief": "a hero"}, "Pick what to generate"),
])
def test_bad_submissions_rerender_the_form(client, world, data, message):
    response = submit(client, **data)
    assert response.status_code == 422 and message in response.text


def test_anchor_outside_the_ai_context_is_rejected(client, world):
    response = submit(client, type="npc", brief="x", anchor=str(world["hidden"]))
    assert response.status_code == 422 and "in the AI context any more" in response.text


def test_happy_path_lands_an_unknown_draft_with_its_history(client, world):
    fake = script(answer(proposed_links=[link_to(world["guild"])],
                         open_threads_touched=[{"entry_id": world["todo"], "thread": "Who sank the barge?",
                                                "proposal": "Ossa did."}]))
    response = submit(client, type="npc", brief="a ferry pilot", anchor=str(world["guild"]), deep="1")
    assert response.status_code == 303
    job_url = response.headers["location"]
    landed = client.get(job_url, follow_redirects=False)
    assert landed.status_code == 303
    draft_id = int(landed.headers["location"].rsplit("/", 1)[1])

    draft = entity(draft_id)
    assert draft["status"] == "draft" and draft["known_to_party"] == 0
    assert draft["fields"]["voice"].startswith("Short sentences")
    assert "notes" not in draft["fields"]  # Claude's notes live in the history, not the entry
    assert fake.calls[0]["deep"] is True
    assert "Ferrymen's Guild" in fake.calls[0]["system"] and "Unflagged draft" not in fake.calls[0]["system"]

    with closing(db.get_connection()) as conn:
        versions = db.lineage_versions(conn, db.lineage_for_entity(conn, draft_id))
        assert db.links_for(conn, draft_id)["outgoing"] == []  # links wait for promotion
    assert len(versions) == 1
    v = versions[0]
    assert v["kind"] == "generated" and v["brief"] == "a ferry pilot" and v["anchors"] == [world["guild"]]
    assert v["model"] == "fake-model" and v["output"]["open_threads_touched"][0]["proposal"] == "Ossa did."

    status = client.get(job_url + "/status")
    assert status.headers["HX-Redirect"] == f"/npc/{draft_id}"


def test_a_flawed_answer_gets_one_retry_with_the_problems(client, world):
    fake = script(answer(), answer(proposed_links=[link_to(world["guild"])]))
    job = generation.start(generation.GenerationRequest("npc", "x", [world["guild"]]))
    assert job.status == "done"
    assert len(fake.calls) == 2
    assert "must link to anchor" in fake.calls[1]["prompt"]


def test_two_flawed_answers_fail_without_storing_anything(client, world):
    script(answer(), answer())
    response = submit(client, type="npc", brief="x", anchor=str(world["guild"]))
    page = client.get(response.headers["location"]).text
    assert "didn&#39;t come through" in page or "didn't come through" in page
    assert "must link to anchor" in page
    assert f"anchor={world['guild']}" in page  # retry link keeps the brief and anchors
    with closing(db.get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE name = 'Ossa Grell'").fetchone()[0] == 0


def test_model_errors_end_the_job_as_failed(client, world):
    script(llm.LLMError("Claude didn't answer within 300s."))
    job = generation.start(generation.GenerationRequest("npc", "x"))
    assert job.status == "failed" and "300s" in job.error
    assert "300s" in client.get(f"/generate/jobs/{job.id}").text


def test_unknown_jobs_are_404(client):
    assert client.get("/generate/jobs/nope").status_code == 404


def test_regeneration_keeps_hand_edits_in_history_and_untouched_fields(client, world):
    script(answer())
    first = generation.start(generation.GenerationRequest("npc", "a ferry pilot"))
    draft_id = first.entity_id
    with closing(db.get_connection()) as conn:
        edited = {**entity(draft_id)["fields"], "personality": "Hand-edited by the GM."}
        db.update_entity(conn, draft_id, "Ossa Grell", edited)

    fake = script({"concept": "Darker.", "fields": {"voice": "Whispers."}, "proposed_links": [],
                   "open_threads_touched": [], "dm_notes": "Darker take."})
    again = generation.start(generation.GenerationRequest("npc", "a ferry pilot", entity_id=draft_id,
                                                          notes="darker voice"))
    assert again.status == "done" and again.entity_id == draft_id
    assert "darker voice" in fake.calls[0]["prompt"]
    assert fake.calls[0]["schema"]["properties"]["fields"]["required"] == []

    fields = entity(draft_id)["fields"]
    assert fields["personality"] == "Hand-edited by the GM."  # not returned, kept verbatim
    assert fields["voice"] == "Whispers." and "notes" not in fields
    with closing(db.get_connection()) as conn:
        kinds = [v["kind"] for v in db.lineage_versions(conn, db.lineage_for_entity(conn, draft_id))]
    assert kinds == ["generated", "edit", "generated"]


def test_regenerating_an_unedited_draft_adds_no_edit_snapshot(client, world):
    script(answer())
    draft_id = generation.start(generation.GenerationRequest("npc", "x")).entity_id
    script({"concept": "c", "fields": {}, "proposed_links": [], "open_threads_touched": [], "dm_notes": "n"})
    generation.start(generation.GenerationRequest("npc", "x", entity_id=draft_id))
    with closing(db.get_connection()) as conn:
        kinds = [v["kind"] for v in db.lineage_versions(conn, db.lineage_for_entity(conn, draft_id))]
    assert kinds == ["generated", "generated"]


def test_only_drafts_can_be_regenerated(client, world):
    job = generation.start(generation.GenerationRequest("npc", "x", entity_id=world["mara"]))
    assert job.status == "failed" and "Only an existing draft" in job.error


def test_spin_off_button_only_where_the_ai_can_see_the_entry(client, world):
    assert f'href="/generate?anchor={world["mara"]}"' in client.get(f"/npc/{world['mara']}").text
    assert "Spin off" in client.get(f"/arc/{world['todo']}").text
    assert "Spin off" not in client.get(f"/location/{world['hidden']}").text
