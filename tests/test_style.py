from contextlib import closing

from src.app import db
from src.app.entity_types import ENTITY_TYPES, NAV_GROUPS


def test_prep_sheets_never_reach_the_party(client):
    with closing(db.get_connection()) as conn:
        enc = db.create_entity(conn, "encounter", "Ambush at the ford", {"premise": "Reeds."}, known=True)
        env = db.create_entity(conn, "environment", "Flooded ford", {"description": "Brown water."}, known=True)
        place = db.create_entity(conn, "location", "The ford", {}, known=True)
        db.add_link(conn, place, enc, "scene of", known=True)
        assert not db.visible_to_party(db.get_entity(conn, enc))
        assert db.party_counts(conn)["encounter"] == 0 and db.party_counts(conn)["environment"] == 0
    client.get("/view/party")
    assert client.get(f"/encounter/{enc}").status_code == 404
    assert client.get(f"/environment/{env}").status_code == 404
    assert "Ambush" not in client.get("/encounter").text and "Ambush" not in client.get("/search?q=ambush").text
    assert "Ambush" not in client.get(f"/location/{place}").text
    nav = client.get("/").text
    assert 'href="/encounter"' not in nav and 'href="/environment"' not in nav and 'href="/adversary"' in nav


def test_a_secret_can_be_teased_and_untoggled(client):
    from src.app import knowledge
    with closing(db.get_connection()) as conn:
        npc = db.create_entity(conn, "npc", "Mara", {"hook": "Rows. ||Owes the guild.||", "voice": "||Whispers.||"})
    page = client.get(f"/npc/{npc}").text
    assert page.count("tease-toggle") == 1  # the hook only: the party never sees voice anyway
    tag = knowledge.passage_tag("Owes the guild.")
    client.post(f"/npc/{npc}/tease/hook/0", data={"tag": tag})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, npc)["fields"]["hook"] == "Rows. ||~Owes the guild.||"
    client.post(f"/npc/{npc}/tease/hook/0", data={"tag": knowledge.passage_tag("~Owes the guild.")})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, npc)["fields"]["hook"] == "Rows. ||Owes the guild.||"
    assert client.post(f"/npc/{npc}/tease/voice/0", data={"tag": "x"}).status_code == 404


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
    assert "The Chronicle" in page and "Session log" not in page
