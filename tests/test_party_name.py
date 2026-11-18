from contextlib import closing

from src.app import context, db


def make(client):
    with closing(db.get_connection()) as conn:
        god = db.create_entity(conn, "npc", "Oove, Watcher of the Night Bloom", {"hook": "Whispers directions."}, known=True)
        db.set_party_name(conn, god, "  the voice in   the night bloom ")
        path = db.create_entity(conn, "location", "The Bloom Path", {}, known=True)
        db.add_link(conn, path, god, "haunted by", known=True)
    return god, path


def test_the_party_only_ever_sees_the_alias(client):
    god, path = make(client)
    client.get("/view/party")
    for url in (f"/npc/{god}", "/npc", f"/location/{path}", "/search?q=voice"):
        page = client.get(url).text
        assert "in the night bloom" in page, url  # search highlights "voice", splitting the phrase
        assert "Oove" not in page, url
    assert "Oove" not in client.get("/search?q=oove").text  # the real name finds nothing


def test_the_dm_sees_both_and_can_reveal_the_true_name(client):
    god, _ = make(client)
    page = client.get(f"/npc/{god}").text
    assert "Oove, Watcher of the Night Bloom" in page and "the voice in the night bloom" in page
    assert "Oove" in client.get("/search?q=voice").text  # the DM finds it by either name
    with closing(db.get_connection()) as conn:
        assert "Known to the party only as: the voice in the night bloom" in context.system_prompt(conn)
    client.post(f"/npc/{god}/reveal-name")
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, god)["party_name"] == ""
        [logged] = db.reveals(conn, None)
        assert logged["field"] == "True name" and "known until now as the voice in the night bloom" in logged["text"]
    client.get("/view/party")
    assert "Oove" in client.get(f"/npc/{god}").text


def test_the_form_sets_and_clears_the_alias(client):
    response = client.post("/npc", data={"name": "Soren Maelir", "party_name": "the hooded ambusher", "status": "canon"},
                           follow_redirects=False)
    entity_id = int(response.headers["location"].rsplit("/", 1)[1])
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, entity_id)["party_name"] == "the hooded ambusher"
    client.post(f"/npc/{entity_id}", data={"name": "Soren Maelir", "party_name": ""})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, entity_id)["party_name"] == ""
