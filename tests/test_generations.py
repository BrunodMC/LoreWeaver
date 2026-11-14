from contextlib import closing

import pytest

from src.app import db


def create(client, type_key, name, **fields):
    response = client.post(f"/{type_key}", data={"name": name, **fields}, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def test_new_lineage_is_keyed_by_its_first_row(conn):
    npc = db.create_entity(conn, "npc", "Mara", {}, status="draft")
    first = db.record_generation(conn, entity_type="npc", kind="generated", entity_id=npc,
                                 brief="a ferrywoman", anchors=[3, 5], output={"name": "Mara"},
                                 model="opus", usage={"output_tokens": 10})
    edit = db.record_generation(conn, entity_type="npc", kind="edit", entity_id=npc,
                                lineage_id=first, output={"name": "Mara the Elder"})
    other = db.record_generation(conn, entity_type="npc", kind="generated", output={})

    versions = db.lineage_versions(conn, first)
    assert [v["id"] for v in versions] == [first, edit]
    assert versions[0]["lineage_id"] == first and versions[0]["anchors"] == [3, 5]
    assert versions[0]["usage"] == {"output_tokens": 10} and versions[1]["usage"] is None
    assert db.get_generation(conn, other)["lineage_id"] == other
    assert db.lineage_for_entity(conn, npc) == first
    assert db.lineage_for_entity(conn, 999) is None


def test_generation_log_outlives_a_discarded_draft(conn):
    npc = db.create_entity(conn, "npc", "Mara", {}, status="draft")
    gen = db.record_generation(conn, entity_type="npc", kind="generated", entity_id=npc, output={"name": "Mara"})
    with conn:
        conn.execute("DELETE FROM entities WHERE id = ?", (npc,))
    kept = db.get_generation(conn, gen)
    assert kept["entity_id"] is None and kept["output"] == {"name": "Mara"}


def test_bad_generation_input_is_rejected(conn):
    with pytest.raises(ValueError):
        db.record_generation(conn, entity_type="npc", kind="guess", output={})
    with pytest.raises(ValueError):
        db.record_generation(conn, entity_type="dragon", kind="generated", output={})
    child = db.record_generation(conn, entity_type="npc", kind="generated", output={})
    db.record_generation(conn, entity_type="npc", kind="edit", lineage_id=child, output={})
    second = db.lineage_versions(conn, child)[1]["id"]
    with pytest.raises(ValueError):  # only a lineage's first row can be its key
        db.record_generation(conn, entity_type="npc", kind="edit", lineage_id=second, output={})
    with pytest.raises(ValueError):
        db.record_generation(conn, entity_type="npc", kind="edit", lineage_id=999, output={})


def test_ai_context_defaults_off_and_toggles_on_drafts_only(client):
    draft = create(client, "arc", "Open threads", status="draft")
    canon = create(client, "npc", "Mara")
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, draft)["ai_context"] == 0

    assert "Include in AI context" in client.get(f"/arc/{draft}").text
    assert "AI context" not in client.get(f"/npc/{canon}").text  # canon always feeds generation

    response = client.post(f"/arc/{draft}/ai-context", data={"included": "1"})
    assert "in AI context" in response.text and "Leave out of AI context" in response.text
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, draft)["ai_context"] == 1
    client.post(f"/arc/{draft}/ai-context", data={"included": "0"})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, draft)["ai_context"] == 0

    assert client.post(f"/npc/{draft}/ai-context", data={"included": "1"}).status_code == 404
