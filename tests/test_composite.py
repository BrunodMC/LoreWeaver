import pytest
from starlette.datastructures import FormData as MultiDict

from src.app import composite, context, db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)
    composite.TASKS.clear()
    composite.MEMBER_JOBS.clear()


@pytest.fixture
def world(conn):
    return {
        "warden": db.create_entity(conn, "adversary", "Reed Warden", {"role": "Leader", "tier": "1"}),
        "ford": db.create_entity(conn, "location", "Old Ford", {"atmosphere": "Brown water."}),
    }


def script(*responses):
    fake = llm.FakeBackend(list(responses))
    llm.set_backend(fake)
    return fake


def drafted(name, fields, links=()):
    return {"concept": "c", "name": name, "fields": fields, "proposed_links": list(links),
            "open_threads_touched": [], "canon_implications": [], "dm_notes": ""}


SPINES = {"spines": [{"title": t, "premise": f"{t} premise.", "why": "Fits."} for t in ("Toll", "Flood", "Ambush")]}


def pool_answer(warden_id):
    return {"members": [
        {"type": "adversary", "name": "Mud Skulkers", "pitch": "Hide in reeds.", "role": "Skulk", "count": 2,
         "existing_id": 0},
        {"type": "environment", "name": "Rising Ford", "pitch": "The water climbs.", "role": "", "count": 1,
         "existing_id": 0},
        {"type": "npc", "name": "Toll-keeper Brann", "pitch": "Wants the toll.", "role": "", "count": 1,
         "existing_id": 0},
        {"type": "adversary", "name": "whatever the model calls it", "pitch": "Commands.", "role": "",
         "count": 1, "existing_id": warden_id},
    ]}


def test_spines_then_pool(conn, world):
    cid = db.create_composite(conn, "a tense crossing", [world["ford"]], pcs=4)
    fake = script({"spines": SPINES["spines"][:1]}, SPINES)  # too few first: retried
    composite.start_task(cid, "spines")
    comp = db.get_composite(conn, cid)
    assert [s["title"] for s in comp["spines"]] == ["Toll", "Flood", "Ambush"]
    assert "Give 3 spines" in fake.calls[1]["prompt"] and "Old Ford" in fake.calls[0]["prompt"]

    composite.choose_spine(conn, cid, "Toll premise, edited.")
    script(pool_answer(world["warden"]))
    composite.start_task(cid, "pool")
    pool = db.get_composite(conn, cid)["pool"]
    assert [m["name"] for m in pool] == ["Mud Skulkers", "Rising Ford", "Toll-keeper Brann", "Reed Warden"]
    reused = pool[3]
    assert reused["existing_id"] == world["warden"] and reused["role"] == "Leader"  # the bible wins
    assert all(m["key"] and m["ticked"] is False for m in pool)

    fake = script({"members": [pool_answer(world["warden"])["members"][0],  # a repeat is dropped
                               {"type": "item", "name": "Toll Chain", "pitch": "Blocks the ford.", "role": "",
                                "count": 1, "existing_id": 0}]})
    composite.start_task(cid, "more")
    assert [m["name"] for m in db.get_composite(conn, cid)["pool"]][-1] == "Toll Chain"
    assert len(db.get_composite(conn, cid)["pool"]) == 5
    assert "Don't repeat any of these" in fake.calls[0]["prompt"]


def test_check_pool_rejects_bad_candidates(conn, world):
    entities = context.context_entities(conn)
    members, problems = context.check_pool({"members": [
        {"type": "adversary", "name": "No role", "pitch": "", "role": "", "count": 1, "existing_id": 0},
        {"type": "pc", "name": "A PC", "pitch": "", "role": "", "count": 1, "existing_id": 0},
        {"type": "npc", "name": "Ghost", "pitch": "", "role": "", "count": 1, "existing_id": 999},
        {"type": "npc", "name": "||Secret|| name", "pitch": "", "role": "", "count": 1, "existing_id": 0},
    ]}, entities)
    assert members == [] and len(problems) == 4


def make_pool(conn, world):
    cid = db.create_composite(conn, "a tense crossing", [], pcs=4)
    composite.choose_spine(conn, cid, "The toll-keeper floods the ford.")
    script(pool_answer(world["warden"]))
    composite.start_task(cid, "pool")
    return cid, {m["name"]: m["key"] for m in db.get_composite(conn, cid)["pool"]}


def test_save_pool_applies_ticks_edits_and_toggles(conn, world):
    cid, keys = make_pool(conn, world)
    form = MultiDict([
        (f"tick_{keys['Mud Skulkers']}", "1"), (f"count_{keys['Mud Skulkers']}", "3"),
        (f"name_{keys['Mud Skulkers']}", "Reed Skulkers"), (f"role_{keys['Mud Skulkers']}", "Ranged"),
        (f"tick_{keys['Reed Warden']}", "1"), (f"name_{keys['Reed Warden']}", "Renamed"),
        (f"drop_{keys['Toll-keeper Brann']}", "1"), ("toggle", "harder"), ("toggle", "bogus"), ("pcs", "3"),
    ])
    comp = composite.save_pool(conn, cid, form)
    names = {m["name"]: m for m in comp["pool"]}
    assert "Toll-keeper Brann" not in names
    assert names["Reed Skulkers"]["count"] == 3 and names["Reed Skulkers"]["role"] == "Ranged"
    assert "Reed Warden" in names  # a reused entry keeps the bible's name
    assert comp["budget"] == {"toggles": ["harder"]} and comp["pcs"] == 3
    meter = generation.composite_meter(comp)
    assert (meter.budget, meter.spent) == (11 + 2, 3 * 2 + 3)


def test_drafting_writes_the_sheet_first_then_members_that_follow_it(conn, world):
    cid, keys = make_pool(conn, world)
    composite.save_pool(conn, cid, MultiDict([(f"tick_{keys[n]}", "1") for n in
                                              ("Mud Skulkers", "Rising Ford", "Reed Warden")]))
    link_to_warden = {"target_id": world["warden"], "direction": "to_new", "label": "commands at",
                      "party_knows": False, "why": "cast"}
    fake = script(
        drafted("Toll at the Ford", {"premise": "Pay or wade.", "setting": "Two banks, a chain across.",
                                     "battle_points": "the model's arithmetic", "phases": "Water rises."},
                [link_to_warden]),
        drafted("Mud Skulkers", {"description": "Reed-masked.", "role": "Skulk", "tier": "1"}),
        drafted("Rising Ford", {"description": "Brown, fast.", "env_type": "Traversal", "tier": "1"}),
    )
    composite.start_drafting(conn, cid)
    comp = db.get_composite(conn, cid)
    sheet = db.get_entity(conn, comp["encounter_id"])
    assert sheet["type"] == "encounter" and sheet["status"] == "draft"
    assert sheet["fields"]["battle_points"].startswith("Budget 14 (3×4+2) · spent 7: 2× Skulk: Mud Skulkers (4)")

    sheet_prompt, *member_prompts = [c["prompt"] for c in fake.calls]
    assert context.SHEET_RULE in sheet_prompt and "Commands. (already in the bible as [adversary #" in sheet_prompt
    for prompt in member_prompts:
        assert context.MEMBER_RULE in prompt and "Setting & layout: Two banks, a chain across." in prompt
    assert "[adversary · 2× Skulk] Mud Skulkers: Hide in reeds." in member_prompts[0]

    members = {e["name"]: e for e in db.composite_members(conn, cid)}
    assert set(members) == {"Toll at the Ford", "Mud Skulkers", "Rising Ford"}  # the reused warden isn't redrafted
    for name in ("Mud Skulkers", "Rising Ford"):
        links = db.links_for(conn, members[name]["id"])["outgoing"]
        assert [(l["relation_label"], l["other_id"]) for l in links] == [("appears in", sheet["id"])]

    state = composite.progress(conn, comp)
    assert [r["state"] for r in state["rows"]] == ["done", "done", "existing"]
    assert db.get_composite(conn, cid)["stage"] == "done"


def test_a_regenerated_member_reads_the_sheet_as_edited(conn, world):
    cid, keys = make_pool(conn, world)
    composite.save_pool(conn, cid, MultiDict([(f"tick_{keys['Mud Skulkers']}", "1")]))
    script(drafted("Toll at the Ford", {"premise": "Pay.", "setting": "Two banks."}),
           drafted("Mud Skulkers", {"description": "Reed-masked.", "role": "Skulk", "tier": "1"}))
    composite.start_drafting(conn, cid)
    comp = db.get_composite(conn, cid)
    sheet = db.get_entity(conn, comp["encounter_id"])
    db.update_entity(conn, sheet["id"], sheet["name"], dict(sheet["fields"], setting="Three banks now."))
    skulkers = next(e for e in db.composite_members(conn, cid) if e["name"] == "Mud Skulkers")

    fake = script(drafted("Mud Skulkers", {"description": "Now with nets."}))
    generation.run(conn, generation.GenerationRequest("adversary", "brief", entity_id=skulkers["id"], notes="nets"))
    assert "Setting & layout: Three banks now." in fake.calls[0]["prompt"]
    assert db.composite_for_entity(conn, skulkers["id"]) == cid


def test_failed_member_can_be_retried(conn, world):
    cid, keys = make_pool(conn, world)
    composite.save_pool(conn, cid, MultiDict([(f"tick_{keys['Mud Skulkers']}", "1")]))
    script(drafted("Toll at the Ford", {"premise": "Pay."}), llm.LLMError("boom"), llm.LLMError("boom"))
    composite.start_drafting(conn, cid)
    comp = db.get_composite(conn, cid)
    assert [r["state"] for r in composite.progress(conn, comp)["rows"]] == ["failed"]

    script(drafted("Mud Skulkers", {"description": "Reed-masked.", "role": "Skulk", "tier": "1"}))
    composite.retry_member(conn, cid, keys["Mud Skulkers"])
    assert [r["state"] for r in composite.progress(conn, comp)["rows"]] == ["done"]


def test_nothing_ticked_means_nothing_drafted(conn, world):
    cid, _ = make_pool(conn, world)
    with pytest.raises(composite.CompositeError):
        composite.start_drafting(conn, cid)


def test_add_existing_entry_to_the_cast(conn, world):
    cid, _ = make_pool(conn, world)
    composite.add_existing(conn, cid, world["ford"])
    composite.add_existing(conn, cid, world["ford"])  # twice is once
    added = [m for m in db.get_composite(conn, cid)["pool"] if m.get("existing_id") == world["ford"]]
    assert len(added) == 1 and added[0]["ticked"] and added[0]["type"] == "location"
