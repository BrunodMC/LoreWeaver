from contextlib import closing

from src.app import db
from src.app.entity_types import ENTITY_TYPES, NAV_GROUPS


def test_an_unfiled_reveal_can_be_dismissed(client):
    with closing(db.get_connection()) as conn:
        entry = db.create_entity(conn, "npc", "Mara", {}, known=True)
        reveal = db.record_reveal(conn, db.get_entity(conn, entry), "Hook", "A ledger.")
    assert "Dismiss" in client.get("/drafts").text
    client.post(f"/reveals/{reveal}/dismiss")
    with closing(db.get_connection()) as conn:
        assert db.reveals(conn, None) == []


def test_a_member_links_back_to_its_cast(client):
    with closing(db.get_connection()) as conn:
        cid = db.create_composite(conn, "brief", [], pcs=4)
        sheet = db.create_entity(conn, "encounter", "Ford toll", {}, status="draft")
        member = db.create_entity(conn, "npc", "Toll-keeper", {}, status="draft")
        for e, kind, name in ((sheet, "encounter", "Ford toll"), (member, "npc", "Toll-keeper")):
            db.record_generation(conn, entity_type=kind, kind="generated", entity_id=e,
                                 output={"name": name, "fields": {}}, composite_id=cid)
        db.update_composite(conn, cid, encounter_id=sheet)
    assert "Back to the cast of Ford toll" in client.get(f"/npc/{member}").text
    assert "Back to the cast" not in client.get(f"/encounter/{sheet}").text


def test_every_type_sits_in_exactly_one_nav_group():
    grouped = [t for *_, types in NAV_GROUPS for t in types]
    assert sorted(grouped) == sorted(ENTITY_TYPES)


def test_each_view_wears_its_own_look(client):
    page = client.get("/").text
    assert 'class="theme-archive"' in page and "LORE<span>·</span>WEAVER" in page and 'id="lamp"' in page
    for _, dm_label, _, _ in NAV_GROUPS:
        assert f"<summary>{dm_label}</summary>" in page
    client.get("/view/party")
    page = client.get("/").text
    assert 'class="theme-codex party-view"' in page and "Perils" in page and "Drafts to review" not in page
