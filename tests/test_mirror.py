from src.app import db, generation


def draft_with_claims(conn, cid, name, target, claims):
    entity = db.create_entity(conn, "npc", name, {}, status="draft")
    output = {"name": name, "fields": {}, "canon_implications": [
        {"entry_id": target, "entry_type": "location", "entry": "[location] Ford", "implication": c} for c in claims]}
    db.record_generation(conn, entity_type="npc", kind="generated", entity_id=entity, output=output, composite_id=cid)
    return entity


def test_one_ruling_settles_the_same_claim_across_the_cast(conn):
    target = db.create_entity(conn, "location", "Ford", {})
    cid = db.create_composite(conn, "brief", [], pcs=4)
    first = draft_with_claims(conn, cid, "Toll-keeper", target,
                              ["Withered creatures steal the night blooms' light, so the blooms they feed on go dark."])
    second = draft_with_claims(conn, cid, "Brood-mother", target,
                               ["Withered creatures steal the night blooms' light, and the blooms they feed on go dark.",
                                "A cart track runs east from the ford to a gully."])

    entity = db.get_entity(conn, first)
    source = generation.review_state(conn, entity)["source"]
    generation.resolve(conn, entity, source, "c0", accept=True)

    other = generation.review_state(conn, db.get_entity(conn, second))
    same, different = other["decisions"]
    assert same["state"] == "accepted" and same["mirrored_from"] == "Toll-keeper"
    assert different["state"] is None  # a different claim about the same place is still yours to rule on
    assert db.get_entity(conn, target)["fields"]["notes"].count("From Toll-keeper") == 1  # noted once


def test_claims_compare_by_wording():
    assert generation.same_claim("The ford floods every spring.", "the ford floods every spring")
    assert not generation.same_claim("The ford floods every spring.", "A toll-keeper lives by the bridge.")
