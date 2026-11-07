from contextlib import closing

import pytest

from src.app import db
from src.app.entity_types import ENTITY_TYPES


def create(client, type_key, name, **fields):
    response = client.post(f"/{type_key}", data={"name": name, **fields}, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def test_index_lists_every_type(client):
    page = client.get("/").text
    for etype in ENTITY_TYPES.values():
        assert etype.plural in page


def test_unknown_type_is_404(client):
    assert client.get("/dragons").status_code == 404
    assert client.get("/dragons/new").status_code == 404


@pytest.mark.parametrize("type_key", list(ENTITY_TYPES))
def test_create_view_edit_each_type(client, type_key):
    first_field = ENTITY_TYPES[type_key].fields[0].key
    entity_id = create(client, type_key, "First draft", **{first_field: "Some lore"})

    detail = client.get(f"/{type_key}/{entity_id}").text
    assert "First draft" in detail and "Some lore" in detail
    assert 'badge-canon' in detail

    assert "First draft" in client.get(f"/{type_key}").text

    response = client.post(f"/{type_key}/{entity_id}", data={"name": "Renamed"}, follow_redirects=False)
    assert response.status_code == 303
    detail = client.get(f"/{type_key}/{entity_id}").text
    assert "Renamed" in detail and "Some lore" not in detail  # cleared field is removed


def test_create_requires_a_name(client):
    response = client.post("/npc", data={"name": "  "})
    assert response.status_code == 422
    assert "A name is required." in response.text


def test_entity_under_wrong_type_is_404(client):
    entity_id = create(client, "npc", "Mara")
    assert client.get(f"/location/{entity_id}").status_code == 404
    assert client.post(f"/location/{entity_id}/status", data={"status": "draft"}).status_code == 404


def test_destructive_actions_ask_for_confirmation(client):
    npc = create(client, "npc", "Mara")
    faction = create(client, "faction", "Ashen Circle")
    client.post(f"/npc/{npc}/links", data={"to_id": faction, "label": "member of"})
    page = client.get(f"/npc/{npc}").text
    assert 'hx-confirm="Archive &quot;Mara&quot;?' in page
    assert 'hx-confirm="Remove this link?"' in page


def test_archive_hides_from_default_list_and_restore_brings_back(client):
    entity_id = create(client, "npc", "Mara")

    response = client.post(f"/npc/{entity_id}/status", data={"status": "archived"})
    assert response.status_code == 200
    assert "Restore to canon" in response.text

    assert "Mara" not in client.get("/npc").text
    assert "Mara" in client.get("/npc?status=archived").text

    client.post(f"/npc/{entity_id}/status", data={"status": "canon"})
    assert "Mara" in client.get("/npc").text


def test_invalid_status_is_rejected(client):
    entity_id = create(client, "npc", "Mara")
    assert client.post(f"/npc/{entity_id}/status", data={"status": "published"}).status_code == 400


def test_edit_preserves_fields_no_longer_in_spec(client, temp_db):
    with closing(db.get_connection()) as conn:
        entity_id = db.create_entity(conn, "npc", "Mara", {"legacy_field": "old lore"})

    client.post(f"/npc/{entity_id}", data={"name": "Mara", "hook": "new hook"})

    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, entity_id)["fields"] == {"legacy_field": "old lore", "hook": "new hook"}
    detail = client.get(f"/npc/{entity_id}").text
    assert "Other fields" in detail and "old lore" in detail


def test_links_show_on_both_sides_and_dedupe(client):
    npc = create(client, "npc", "Mara")
    faction = create(client, "faction", "Ashen Circle")

    response = client.post(f"/npc/{npc}/links", data={"to_id": faction, "label": "member of"})
    assert response.status_code == 200
    assert "Ashen Circle" in response.text

    assert "← member of:" in client.get(f"/faction/{faction}").text

    duplicate = client.post(f"/npc/{npc}/links", data={"to_id": faction, "label": "MEMBER OF"})
    assert "That link already exists." in duplicate.text


def test_link_validation_errors_return_422(client):
    npc = create(client, "npc", "Mara")
    assert client.post(f"/npc/{npc}/links", data={"to_id": "", "label": "knows"}).status_code == 422
    assert client.post(f"/npc/{npc}/links", data={"to_id": 9999, "label": "knows"}).status_code == 422
    assert client.post(f"/npc/{npc}/links", data={"to_id": npc, "label": "knows"}).status_code == 422


def test_link_can_be_removed_from_either_side_but_not_by_a_stranger(client, temp_db):
    npc = create(client, "npc", "Mara")
    faction = create(client, "faction", "Ashen Circle")
    stranger = create(client, "location", "Thornwood")
    client.post(f"/npc/{npc}/links", data={"to_id": faction, "label": "member of"})
    with closing(db.get_connection()) as conn:
        link_id = conn.execute("SELECT id FROM links").fetchone()[0]

    assert client.post(f"/location/{stranger}/links/{link_id}/delete").status_code == 404

    response = client.post(f"/faction/{faction}/links/{link_id}/delete")
    assert response.status_code == 200
    assert "No links yet." in response.text


def party(client):
    client.get("/view/party", follow_redirects=False)


def test_create_with_known_flag_and_unclosed_secret_is_rejected(client):
    rejected = client.post("/npc", data={"name": "Nikta", "appearance": "Owl. ||Eye stolen"})
    assert rejected.status_code == 422
    assert "Unclosed || secret marker in: Appearance" in rejected.text

    npc = create(client, "npc", "Nikta", known="1", appearance="Owl. ||Eye stolen.||")
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, npc)["known_to_party"] == 1


def test_edit_rejects_unclosed_secret(client):
    npc = create(client, "npc", "Nikta")
    response = client.post(f"/npc/{npc}", data={"name": "Nikta", "notes": "||oops"})
    assert response.status_code == 422


def test_dm_view_shows_secrets_highlighted(client):
    npc = create(client, "npc", "Nikta", appearance="A great owl. ||The Reaping Eye was stolen.||")
    page = client.get(f"/npc/{npc}").text
    assert '<span class="secret"' in page and "The Reaping Eye was stolen." in page


def test_party_view_hides_secrets_unknowns_drafts_and_dm_controls(client):
    nikta = create(client, "npc", "Nikta", known="1", appearance="A great owl. ||The Reaping Eye was stolen.||")
    phylax = create(client, "npc", "Phylax")
    drafty = create(client, "npc", "Drafty", known="1", status="draft")
    owls = create(client, "faction", "Owl Cult", known="1")
    client.post(f"/npc/{nikta}/links", data={"to_id": owls, "label": "worshipped by", "known": "1"})
    client.post(f"/npc/{nikta}/links", data={"to_id": owls, "label": "secretly fears"})
    client.post(f"/npc/{nikta}/links", data={"to_id": phylax, "label": "rival of", "known": "1"})

    party(client)
    page = client.get(f"/npc/{nikta}").text
    assert "A great owl." in page
    assert "Reaping Eye" not in page
    assert "worshipped by" in page
    assert "secretly fears" not in page
    assert "Phylax" not in page          # link to an unknown entity
    assert "hx-post" not in page          # no DM controls
    assert "/edit" not in page

    listing = client.get("/npc").text
    assert "Nikta" in listing and "Phylax" not in listing and "Drafty" not in listing

    assert client.get(f"/npc/{phylax}").status_code == 404
    assert client.get(f"/npc/{drafty}").status_code == 404

    client.get("/view/dm", follow_redirects=False)
    assert "Reaping Eye" in client.get(f"/npc/{nikta}").text


def test_known_toggles(client):
    npc = create(client, "npc", "Nikta")
    faction = create(client, "faction", "Owl Cult")
    response = client.post(f"/npc/{npc}/known", data={"known": "1"})
    assert "known to party" in response.text and "Mark unknown" in response.text

    client.post(f"/npc/{npc}/links", data={"to_id": faction, "label": "worshipped by"})
    with closing(db.get_connection()) as conn:
        link_id = conn.execute("SELECT id FROM links").fetchone()[0]
    response = client.post(f"/faction/{faction}/links/{link_id}/known", data={"known": "1"})
    assert response.status_code == 200
    with closing(db.get_connection()) as conn:
        assert db.get_link(conn, link_id)["known_to_party"] == 1

    stranger = create(client, "location", "Thornwood")
    assert client.post(f"/location/{stranger}/links/{link_id}/known", data={"known": "0"}).status_code == 404


def test_view_toggle_only_redirects_within_the_site(client):
    response = client.get("/view/party?next=https://evil.example", follow_redirects=False)
    assert response.headers["location"] == "/"
    response = client.get("/view/party?next=//evil.example", follow_redirects=False)
    assert response.headers["location"] == "/"
    response = client.get("/view/party?next=/npc%3Fstatus%3Ddraft", follow_redirects=False)
    assert response.headers["location"] == "/npc?status=draft"
    assert client.get("/view/admin").status_code == 404


def test_timestamps_display_in_local_time(client):
    from datetime import datetime, timezone

    npc = create(client, "npc", "Mara")
    with closing(db.get_connection()) as conn:
        conn.execute("UPDATE entities SET created_at = '2026-01-15 12:30:00', "
                     "updated_at = '2026-01-15 12:30:00' WHERE id = ?", (npc,))
        conn.commit()
    expected = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    assert f"Updated {expected}" in client.get(f"/npc/{npc}").text
    assert expected in client.get("/npc").text


def test_startup_creates_backup_of_existing_db(temp_db):
    from fastapi.testclient import TestClient
    from src.app.main import create_app

    with TestClient(create_app()):
        pass  # first start: DB created, nothing to back up yet
    with TestClient(create_app()):
        pass
    assert len(list((temp_db.parent / "backups").glob("*.db"))) == 1
