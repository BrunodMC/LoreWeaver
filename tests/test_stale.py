from contextlib import closing

import pytest

from src.app import audit, db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


def script(*responses):
    backend = llm.FakeBackend(list(responses))
    llm.set_backend(backend)
    return backend


def finding(passage, field="personality", certainty="clear", evidence="Session 1: they watched it happen."):
    return {"field": field, "passage": passage, "evidence": evidence, "certainty": certainty}


@pytest.fixture
def kreil(conn):
    return db.create_entity(conn, "npc", "Kreil", {
        "personality": "Cold. ||Wears magic spectacles.|| Precise. ||Hates Phylax.||",
        "voice": "||Whispers.||",  # DM-only field: never part of the check
    }, known=True)


def test_the_check_lists_hidden_passages_and_the_reveal_log(conn, kreil):
    db.record_reveal(conn, db.get_entity(conn, kreil), "Hook", "He hired them.")
    backend = script({"findings": []})
    audit.run(conn, "stale", kreil)
    prompt = backend.calls[0]["prompt"]
    assert "  - Wears magic spectacles." in prompt and "  - Hates Phylax." in prompt
    assert "Whispers" not in prompt
    assert "Kreil · Hook: He hired them." in prompt
    assert backend.calls[0]["model"] is None  # the strong default


def test_nothing_to_check_without_hidden_passages_or_if_the_entry_is_unknown(conn):
    plain = db.create_entity(conn, "npc", "Plain", {"personality": "Open."}, known=True)
    hidden = db.create_entity(conn, "npc", "Hidden", {"personality": "||Secret.||"})
    backend = script()
    for e in (plain, hidden):
        audit.run(conn, "stale", e)
    assert backend.calls == []


def test_a_passage_must_be_copied_exactly(conn, kreil):
    backend = script({"findings": [finding("Wears spectacles.")]}, {"findings": [finding("Wears magic spectacles.")]})
    found = audit.get(conn, audit.run(conn, "stale", kreil))
    assert len(backend.calls) == 2 and "isn't one of the hidden passages" in backend.calls[1]["prompt"]
    assert [f["quote"] for f in found["findings"]] == ["Wears magic spectacles."]


def test_applying_reveals_only_the_ticked_and_logs_each(conn, kreil):
    script({"findings": [finding("Wears magic spectacles."), finding("Hates Phylax.", certainty="likely")]})
    found = audit.get(conn, audit.run(conn, "stale", kreil))
    keys = {f["quote"]: f["key"] for f in found["findings"]}
    assert audit.apply(conn, found, {keys["Hates Phylax."]}) == []
    text = db.get_entity(conn, kreil)["fields"]["personality"]
    assert text == "Cold. ||Wears magic spectacles.|| Precise. Hates Phylax."
    assert [(r["field"], r["text"]) for r in db.reveals(conn, None)] == [("Personality", "Hates Phylax.")]


def test_two_reveals_in_one_field_survive_the_shifting_positions(conn, kreil):
    script({"findings": [finding("Wears magic spectacles."), finding("Hates Phylax.")]})
    found = audit.get(conn, audit.run(conn, "stale", kreil))
    audit.apply(conn, found, {f["key"] for f in found["findings"]})
    assert db.get_entity(conn, kreil)["fields"]["personality"] == "Cold. Wears magic spectacles. Precise. Hates Phylax."


def test_a_passage_edited_since_is_reported_not_forced(conn, kreil):
    script({"findings": [finding("Hates Phylax.")]})
    found = audit.get(conn, audit.run(conn, "stale", kreil))
    e = db.get_entity(conn, kreil)
    db.update_entity(conn, kreil, e["name"], {**e["fields"], "personality": "Cold. ||Loathes Phylax.||"})
    failures = audit.apply(conn, found, {found["findings"][0]["key"]})
    assert len(failures) == 1 and "changed since the check" in failures[0]
    assert db.reveals(conn, None) == []


# ── Through the app ────────────────────────────────────────────────────────

@pytest.fixture
def page_kreil(client):
    with closing(db.get_connection()) as conn:
        return db.create_entity(conn, "npc", "Kreil", {"personality": "Cold. ||Hates Phylax.||"}, known=True)


def test_the_button_and_panel(client, page_kreil):
    assert "Find stale secrets" in client.get(f"/npc/{page_kreil}").text
    script({"findings": [finding("Hates Phylax.")]})
    client.post(f"/npc/{page_kreil}/stale-check")
    page = client.get(f"/npc/{page_kreil}").text
    assert "Stale secrets" in page and "“Hates Phylax.”" in page and "watched it happen" in page
    assert 'name="finding" value="f0">' in page  # unticked
    with closing(db.get_connection()) as conn:
        audit_id = audit.latest_open(conn, "stale", page_kreil)["id"]
    client.post(f"/npc/{page_kreil}/audits/{audit_id}/apply", data={"finding": "f0"})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, page_kreil)["fields"]["personality"] == "Cold. Hates Phylax."
    assert "Find stale secrets" not in client.get(f"/npc/{page_kreil}").text  # nothing hidden left


def test_no_button_on_entries_the_party_doesnt_know(client):
    with closing(db.get_connection()) as conn:
        hidden = db.create_entity(conn, "npc", "Hidden", {"personality": "||Secret.||"})
    assert "Find stale secrets" not in client.get(f"/npc/{hidden}").text


def test_players_cannot_run_it(player, page_kreil):
    assert player.post(f"/npc/{page_kreil}/stale-check").status_code == 403
