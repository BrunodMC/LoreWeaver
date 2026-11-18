from contextlib import closing

from src.app import db, portraits

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82")


def make(client, known=True):
    with closing(db.get_connection()) as conn:
        return db.create_entity(conn, "npc", "Mara", {
            "ancestry_role": "Human · Ferrywoman ||and smuggler||",
            "appearance": "Tall, oilcloth coat. ||A brand on her wrist.||"}, known=known)


def upload(client, entity_id, data=PNG, kind="image/png"):
    return client.post(f"/npc/{entity_id}/portrait", files={"image": ("p.png", data, kind)}, follow_redirects=False)


def test_upload_show_and_remove(client):
    mara = make(client)
    assert upload(client, mara).status_code == 303
    assert client.get(f"/portrait/{mara}").content == PNG
    page = client.get(f"/npc/{mara}").text
    assert f'src="/portrait/{mara}"' in page and "Replace" in page
    client.get("/view/party")
    assert 'class="portrait-img"' in client.get(f"/npc/{mara}").text
    client.get("/view/dm")
    client.post(f"/npc/{mara}/portrait/remove")
    assert client.get(f"/portrait/{mara}").status_code == 404


def test_portraits_follow_party_visibility(client):
    hidden = make(client, known=False)
    upload(client, hidden)
    client.get("/view/party")
    assert client.get(f"/portrait/{hidden}").status_code == 404  # the party can't see the entry, nor its face
    assert upload(client, hidden).status_code == 404  # and players never upload


def test_bad_uploads_are_refused(client):
    mara = make(client)
    assert upload(client, mara, b"not an image", "text/plain").status_code == 422
    assert upload(client, mara, b"", "image/png").status_code == 422


def test_the_prompt_holds_the_shared_style_and_no_secrets(client):
    mara = make(client)
    page = client.get(f"/npc/{mara}").text
    assert portraits.DEFAULT_STYLE[:40] in page and "Tall, oilcloth coat." in page
    with closing(db.get_connection()) as conn:
        prompt = portraits.prompt(conn, db.get_entity(conn, mara))
    assert "brand" not in prompt and "smuggler" not in prompt
    client.post("/settings/portrait-style", data={"style": "Ink and watercolour field sketch.", "next": f"/npc/{mara}"})
    assert "Ink and watercolour field sketch." in client.get(f"/npc/{mara}").text
