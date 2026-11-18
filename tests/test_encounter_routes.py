from contextlib import closing

import pytest

from src.app import composite, db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)
    composite.TASKS.clear()
    composite.MEMBER_JOBS.clear()


@pytest.fixture
def world(client):
    with closing(db.get_connection()) as conn:
        return {"ford": db.create_entity(conn, "location", "Old Ford", {"atmosphere": "Brown water."}),
                "pc": db.create_entity(conn, "pc", "Someone", {}, status="draft")}


def script(*responses):
    fake = llm.FakeBackend(list(responses))
    llm.set_backend(fake)
    return fake


POOL = {"members": [
    {"type": "adversary", "name": "Mud Skulkers", "pitch": "Hide in reeds.", "role": "Skulk", "count": 2,
     "existing_id": 0},
    {"type": "environment", "name": "Rising Ford", "pitch": "The water climbs.", "role": "", "count": 1,
     "existing_id": 0},
]}


def drafted(name, fields):
    return {"concept": "c", "name": name, "fields": fields, "proposed_links": [], "open_threads_touched": [],
            "canon_implications": [], "dm_notes": ""}


def composite_id(response) -> int:
    return int(response.headers["location"].rsplit("/", 1)[1])


def test_compose_form_defaults_pcs_and_is_dm_only(client, world):
    page = client.get("/encounters/new")
    assert page.status_code == 200 and 'name="pcs" min="1" max="12" value="1"' in page.text
    client.get("/view/party")
    assert client.get("/encounters/new").status_code == 404


def test_a_brief_is_required(client, world):
    response = client.post("/encounters", data={"brief": "  ", "action": "spines"})
    assert response.status_code == 422 and "Write a brief" in response.text


def test_spines_then_choose_then_pool(client, world):
    script({"spines": [{"title": t, "premise": f"{t} premise.", "why": "Fits."} for t in ("Toll", "Flood", "Ambush")]})
    response = client.post("/encounters", data={"brief": "a crossing", "action": "spines", "pcs": "4",
                                                "anchor": str(world["ford"])}, follow_redirects=False)
    cid = composite_id(response)
    page = client.get(f"/encounters/{cid}")
    assert "Pick a premise" in page.text and "Flood premise." in page.text and "Old Ford" in page.text

    script(POOL)
    client.post(f"/encounters/{cid}/choose", data={"spine": "Toll\nToll premise, edited."})
    page = client.get(f"/encounters/{cid}")
    assert "Pick the cast" in page.text and "Mud Skulkers" in page.text and "Toll premise, edited." in page.text


def test_skip_pool_meter_draft_and_progress(client, world):
    script(POOL)
    cid = composite_id(client.post("/encounters", data={"brief": "a crossing", "action": "skip", "pcs": "4"},
                                   follow_redirects=False))
    with closing(db.get_connection()) as conn:
        keys = {m["name"]: m["key"] for m in db.get_composite(conn, cid)["pool"]}
        assert db.get_composite(conn, cid)["spine"] == "a crossing"

    meter = client.post(f"/encounters/{cid}/pool", headers={"HX-Request": "true"},
                        data={f"tick_{keys['Mud Skulkers']}": "1", "pcs": "4", "toggle": "harder"})
    assert "Battle Points: 4 / 17" in meter.text  # 14 +2 harder +1 no heavy hitters

    client.post(f"/encounters/{cid}/cast/{world['ford']}", headers={"HX-Request": "true"})
    with closing(db.get_connection()) as conn:
        ford_key = next(m["key"] for m in db.get_composite(conn, cid)["pool"] if m.get("existing_id"))
    sheet = drafted("Toll at the Ford", {"premise": "Pay or wade."})
    sheet["proposed_links"] = [{"target_id": world["ford"], "direction": "from_new", "label": "takes place at",
                                "party_knows": False, "why": "cast"}]  # a reused entry is the sheet's anchor
    script(sheet,
           drafted("Mud Skulkers", {"description": "Reed-masked.", "role": "Skulk", "tier": "1"}),
           drafted("Rising Ford", {"description": "Fast.", "env_type": "Traversal", "tier": "1"}))
    client.post(f"/encounters/{cid}/draft", data={f"tick_{keys['Mud Skulkers']}": "1", f"tick_{ford_key}": "1",
                                                  f"tick_{keys['Rising Ford']}": "1", "pcs": "4"})
    page = client.get(f"/encounters/{cid}")
    assert "Review the encounter" in page.text and "from the bible" in page.text
    assert page.text.count("✓") == 3  # the sheet and two members
    assert client.get(f"/encounters/{cid}/status").headers.get("HX-Refresh") == "true"


def test_discard_an_undrafted_workspace(client, world):
    script(POOL)
    cid = composite_id(client.post("/encounters", data={"brief": "x", "action": "skip"}, follow_redirects=False))
    assert client.post(f"/encounters/{cid}/discard", follow_redirects=False).status_code == 303
    assert client.get(f"/encounters/{cid}").status_code == 404


def test_drafting_nothing_is_refused(client, world):
    script(POOL)
    cid = composite_id(client.post("/encounters", data={"brief": "x", "action": "skip"}, follow_redirects=False))
    assert client.post(f"/encounters/{cid}/draft", data={}).status_code == 409


def compose_and_draft(client, world, member_answer=None):
    """A drafted composite: the sheet (linked to the ford) and one member. Returns (composite id, ids)."""
    script(POOL)
    cid = composite_id(client.post("/encounters", data={"brief": "x", "action": "skip"}, follow_redirects=False))
    with closing(db.get_connection()) as conn:
        key = next(m["key"] for m in db.get_composite(conn, cid)["pool"] if m["name"] == "Mud Skulkers")
    sheet = drafted("Toll at the Ford", {"premise": "Pay or wade."})
    sheet["proposed_links"] = [{"target_id": world["ford"], "direction": "from_new", "label": "takes place at",
                                "party_knows": False, "why": "setting"}]
    script(sheet, member_answer or drafted("Mud Skulkers", {"description": "Masked.", "role": "Skulk", "tier": "1"}))
    client.post(f"/encounters/{cid}/draft", data={f"tick_{key}": "1"})
    with closing(db.get_connection()) as conn:
        comp = db.get_composite(conn, cid)
        member = next(e for e in db.composite_members(conn, cid) if e["type"] == "adversary")
    return cid, {"sheet": comp["encounter_id"], "member": member["id"]}


def test_cast_panel_and_promote_all(client, world):
    cid, ids = compose_and_draft(client, world)
    page = client.get(f"/encounter/{ids['sheet']}")
    assert "Promote all (2)" in page.text and "Mud Skulkers" in page.text

    inbox = client.get("/drafts").text
    assert inbox.count('class="inbox-item"') == 1 and "inbox-members" in inbox  # the member nests under it

    client.post(f"/encounters/{cid}/promote-all")
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, ids["sheet"])["status"] == "canon"
        assert db.get_entity(conn, ids["member"])["status"] == "canon"
        labels = {l["relation_label"] for l in db.links_for(conn, ids["sheet"])["outgoing"]}
        assert labels == {"takes place at"}  # the proposed link was made
        assert db.links_for(conn, ids["member"])["outgoing"][0]["relation_label"] == "appears in"


def test_open_decisions_block_promote_all(client, world):
    with closing(db.get_connection()) as conn:
        todo = db.create_entity(conn, "arc", "Open threads", {"notes": "Who built the ford?"}, status="draft")
        db.set_ai_context(conn, todo, True)
    member = drafted("Mud Skulkers", {"description": "Masked.", "role": "Skulk", "tier": "1"})
    member["open_threads_touched"] = [{"entry_id": todo, "thread": "Who built the ford?", "proposal": "Skulkers."}]
    cid, ids = compose_and_draft(client, world, member)
    page = client.get(f"/encounter/{ids['sheet']}")
    assert "Promote all waits" in page.text
    assert client.post(f"/encounters/{cid}/promote-all").status_code == 409
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, ids["sheet"])["status"] == "draft"


def test_discard_all_keeps_the_bible_entries(client, world):
    cid, ids = compose_and_draft(client, world)
    assert client.post(f"/encounters/{cid}/discard-all", follow_redirects=False).status_code == 303
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, ids["sheet"]) is None and db.get_entity(conn, ids["member"]) is None
        assert db.get_entity(conn, world["ford"]) is not None


def test_inbox_lists_workspaces_being_composed(client, world):
    script(POOL)
    cid = composite_id(client.post("/encounters", data={"brief": "a crossing at dusk", "action": "skip"},
                                   follow_redirects=False))
    inbox = client.get("/drafts").text
    assert "Encounters being composed" in inbox and f"/encounters/{cid}" in inbox and "picking the cast" in inbox
