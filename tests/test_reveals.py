from contextlib import closing
from datetime import datetime

import pytest

from src.app import db, knowledge


def test_secret_passages_and_revealing_one():
    text = "Kind. ||Owes the guild.|| Loud. ||Fears water.|| tail ||unclosed"
    assert knowledge.secret_passages(text) == ["Owes the guild.", "Fears water."]
    new, passage = knowledge.reveal_passage(text, 1, knowledge.passage_tag("Fears water."))
    assert passage == "Fears water."
    assert new == "Kind. ||Owes the guild.|| Loud. Fears water. tail ||unclosed"
    assert knowledge.party_text(new) == "Kind. Loud. Fears water. tail"


def test_a_changed_passage_is_not_revealed():
    text = "||a|| ||b||"
    with pytest.raises(knowledge.PassageGone):
        knowledge.reveal_passage(text, 0, knowledge.passage_tag("b"))
    with pytest.raises(knowledge.PassageGone):
        knowledge.reveal_passage(text, 5, knowledge.passage_tag("a"))
    with pytest.raises(knowledge.PassageGone):  # the unclosed tail isn't addressable
        knowledge.reveal_passage("x ||open", 0, knowledge.passage_tag("open"))


def test_render_adds_reveal_controls_only_when_asked():
    plain = knowledge.render("A ||b|| c")
    assert "reveal-passage" not in plain
    controlled = knowledge.render("A ||b|| c ||d", reveal_url="/npc/1/reveal/hook")
    assert controlled.count("reveal-passage") == 1  # not on the unclosed tail
    assert 'hx-post="/npc/1/reveal/hook/0"' in controlled and knowledge.passage_tag("b") in controlled


@pytest.fixture
def mara(client):
    with closing(db.get_connection()) as conn:
        return db.create_entity(conn, "npc", "Mara", {"hook": "Lost a ledger. ||She burned it.||",
                                                      "voice": "||Whispers.||"}, known=True)


def reveal(client, entity_id, field="hook", index=0, passage="She burned it."):
    return client.post(f"/npc/{entity_id}/reveal/{field}/{index}", data={"tag": knowledge.passage_tag(passage)},
                       follow_redirects=False)


def test_revealing_a_passage_unbars_it_and_logs_it_unfiled(client, mara):
    page = client.get(f"/npc/{mara}")
    assert page.text.count("reveal-passage") == 1  # hook only: voice is DM-only
    assert reveal(client, mara).status_code == 303
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, mara)["fields"]["hook"] == "Lost a ledger. She burned it."
        [logged] = db.reveals(conn, None)
        assert (logged["entity_name"], logged["field"], logged["text"]) == ("Mara", "Hook", "She burned it.")
    assert reveal(client, mara).status_code == 409  # already revealed: the passage is gone
    inbox = client.get("/drafts").text
    assert "Unfiled reveals" in inbox and "She burned it." in inbox


def test_reveal_logs_never_feed_the_model(conn):
    """The bible the model reads is entries only: reveal logs (like audits and workspaces) stay out."""
    from src.app import context
    entry = db.create_entity(conn, "npc", "Mara", {"hook": "A ledger."}, known=True)
    db.record_reveal(conn, db.get_entity(conn, entry), "Hook", "UNIQUE-LOG-TEXT-7731")
    assert "UNIQUE-LOG-TEXT-7731" not in context.system_prompt(conn)


def test_reveal_guards(client, mara):
    assert reveal(client, mara, field="voice", passage="Whispers.").status_code == 404  # DM-only field
    with closing(db.get_connection()) as conn:
        hidden = db.create_entity(conn, "npc", "Hidden", {"hook": "||x||"})
    assert reveal(client, hidden, passage="x").status_code == 409  # the party can't see the entry
    assert "reveal-passage" not in client.get(f"/npc/{hidden}").text
    client.get("/view/party")
    assert reveal(client, mara).status_code == 404


def test_reveals_file_to_todays_session_or_wait(client, mara):
    with closing(db.get_connection()) as conn:
        old = db.create_entity(conn, "session", "Session 1", {"session_date": "2020-01-01"}, known=True)
        hidden = db.create_entity(conn, "location", "Cellar", {})
    client.post(f"/location/{hidden}/known", data={"known": "1"})  # a whole entry, no session today
    with closing(db.get_connection()) as conn:
        [whole] = db.reveals(conn, None)
        assert (whole["entity_name"], whole["field"]) == ("Cellar", "")
        today = db.create_entity(conn, "session", "Session 2",
                                 {"session_date": datetime.now().strftime("%Y-%m-%d")}, known=True)
        assert [s["name"] for s in db.sessions_newest_first(conn)] == ["Session 2", "Session 1"]
    reveal(client, mara)
    with closing(db.get_connection()) as conn:
        assert [r["text"] for r in db.reveals(conn, today)] == ["She burned it."]  # filed automatically

    client.post("/reveals/file", data={"reveal": str(whole["id"]), "session_id": str(old)})
    with closing(db.get_connection()) as conn:
        assert db.reveals(conn, None) == [] and [r["entity_name"] for r in db.reveals(conn, old)] == ["Cellar"]
    page = client.get(f"/session/{old}").text
    assert "Reveal log: material for the summary" in page and "Cellar" in page
    client.get("/view/party")
    assert "Reveal log" not in client.get(f"/session/{old}").text
    assert client.post("/reveals/file", data={"reveal": "1", "session_id": str(old)}).status_code == 404  # DM only
    client.get("/view/dm")
    assert client.post("/reveals/file", data={"reveal": "1", "session_id": str(mara)}).status_code == 422
