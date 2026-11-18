from contextlib import closing

import pytest

from src.app import audit, db, generation, knowledge, llm


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)
    audit.TASKS.clear()


def script(*responses):
    fake = llm.FakeBackend(list(responses))
    llm.set_backend(fake)
    return fake


def test_bar_quote_hides_only_visible_text():
    assert knowledge.bar_quote("He limps. ||Old war.|| He limps badly.", "limps badly") == \
        "He limps. ||Old war.|| He ||limps badly||."
    assert knowledge.bar_quote("He limps.", "limps") == "He ||limps||."
    with pytest.raises(knowledge.CannotBar):
        knowledge.bar_quote("Calm. ||Old war.||", "Old war")  # already hidden
    with pytest.raises(knowledge.CannotBar):
        knowledge.bar_quote("Calm. ||open tail", "open")  # inside an unclosed (secret) tail
    with pytest.raises(knowledge.CannotBar):
        knowledge.bar_quote("Calm.", "not there")


@pytest.fixture
def world(client):
    with closing(db.get_connection()) as conn:
        guild = db.create_entity(conn, "faction", "Ferrymen's Guild", {"goals": "||Smuggle relics.||"})
        mara = db.create_entity(conn, "npc", "Mara", {
            "personality": "Brisk. Her boat smells of incense, like the relic vaults. ||Smuggles for the guild.||",
            "hook": "Lost ledger.", "voice": "Whispers."})
    return {"guild": guild, "mara": mara}


def finding(quote, field="personality", severity="obvious"):
    return {"field": field, "quote": quote, "reason": "Points at the smuggling.", "severity": severity}


def test_secrecy_audit_checks_quotes_and_uses_the_whole_bible(conn, world):
    fake = script({"findings": [finding("smells of relics")]},  # not verbatim: retried
                  {"findings": [finding("like the relic vaults"), finding("Lost ledger.", "hook", "ambiguous"),
                                finding("like the relic vaults")]})
    audit_id = audit.run(conn, "secrecy", world["mara"])
    found = audit.get(conn, audit_id)
    assert [(f["quote"], f["severity"], f["key"]) for f in found["findings"]] == [
        ("like the relic vaults", "obvious", "f0"), ("Lost ledger.", "ambiguous", "f1")]
    first = fake.calls[0]
    assert "Smuggle relics." in first["system"]  # the bible, secrets included
    assert "Smuggles for the guild." not in first["prompt"]  # the subject is only what players see
    assert "Whispers." not in first["prompt"]  # DM-only fields aren't shown to players
    assert first["model"] is audit.SECRECY_MODEL
    assert "isn't copied exactly" in fake.calls[1]["prompt"]


def test_an_entry_with_nothing_visible_needs_no_call(conn):
    blank = db.create_entity(conn, "npc", "Blank", {"personality": "||All secret.||"})
    script()  # any call would fail
    assert audit.get(conn, audit.run(conn, "secrecy", blank))["findings"] == []


def test_check_then_hide_and_reveal(client, world):
    mara = world["mara"]
    script({"findings": [finding("like the relic vaults"), finding("Lost ledger.", "hook", "ambiguous")]})
    client.post(f"/npc/{mara}/reveal-check")
    page = client.get(f"/npc/{mara}").text
    assert "Before revealing: secrecy check" in page
    assert 'value="f0" checked' in page and 'value="f1">' in page  # obvious pre-ticked, ambiguous not

    with closing(db.get_connection()) as conn:
        audit_id = audit.latest_open(conn, "secrecy", mara)["id"]
    client.post(f"/npc/{mara}/audits/{audit_id}/apply", data={"finding": "f0", "action": "reveal"})
    with closing(db.get_connection()) as conn:
        entity = db.get_entity(conn, mara)
        assert "like ||like the relic vaults||" not in entity["fields"]["personality"]
        assert "||like the relic vaults||" in entity["fields"]["personality"]
        assert entity["fields"]["hook"] == "Lost ledger."  # unticked: left alone
        assert entity["known_to_party"] == 1
        assert [r["entity_name"] for r in db.reveals(conn, None)] == ["Mara"]
        assert audit.get(conn, audit_id)["state"] == {"f0": "accepted", "f1": "rejected"}
        assert audit.latest_open(conn, "secrecy", mara) is None
    client.get("/view/party")
    assert "relic vaults" not in client.get(f"/npc/{mara}").text


def test_a_finding_that_cannot_be_placed_is_reported(client, world):
    mara = world["mara"]
    script({"findings": [finding("Brisk.")]})
    client.post(f"/npc/{mara}/reveal-check")
    with closing(db.get_connection()) as conn:
        audit_id = audit.latest_open(conn, "secrecy", mara)["id"]
        entity = db.get_entity(conn, mara)
        fields = dict(entity["fields"], personality="||Brisk.|| " + entity["fields"]["personality"][7:])
        db.update_entity(conn, mara, "Mara", fields)  # the GM hid it by hand meanwhile
    response = client.post(f"/npc/{mara}/audits/{audit_id}/apply", data={"finding": "f0", "action": "hide"})
    assert "Couldn't hide these automatically" in response.text


def test_dismiss_and_a_clean_bill(client, world):
    mara = world["mara"]
    script({"findings": []})
    client.post(f"/npc/{mara}/reveal-check")
    assert "Nothing in the visible text gives a secret away" in client.get(f"/npc/{mara}").text
    with closing(db.get_connection()) as conn:
        audit_id = audit.latest_open(conn, "secrecy", mara)["id"]
    client.post(f"/npc/{mara}/audits/{audit_id}/dismiss")
    assert "secrecy check" not in client.get(f"/npc/{mara}").text
    assert client.post(f"/npc/{mara}/audits/{audit_id}/apply", data={}).status_code == 404
