from contextlib import closing

import pytest

from src.app import briefing, context, db, deaths, generation, llm, proposals


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


def script(*responses):
    backend = llm.FakeBackend(list(responses))
    llm.set_backend(backend)
    return backend


def no_change(summary="Untouched."):
    return {"summary": summary, "fields": {}, "reasons": [], "proposed_links": [], "dm_notes": ""}


def change(field, text):
    return {"summary": "Shaken.", "fields": {field: text}, "reasons": [{"field": field, "why": "grief"}],
            "proposed_links": [], "dm_notes": ""}


@pytest.fixture
def world(conn):
    ids = {
        "kreil": db.create_entity(conn, "npc", "Kreil", {"personality": "Cold."}, known=True),
        "aide": db.create_entity(conn, "npc", "Aide", {"personality": "Loyal."}),
        "guild": db.create_entity(conn, "faction", "Guild", {"goals": "Profit."}),
        "pc": db.create_entity(conn, "pc", "Tamsin", {}, known=True),
        "s1": db.create_entity(conn, "session", "Session 1", {}),
        "far": db.create_entity(conn, "npc", "Unrelated", {}),
    }
    db.add_link(conn, ids["aide"], ids["kreil"], "serves")
    db.add_link(conn, ids["kreil"], ids["guild"], "member of")
    db.add_link(conn, ids["pc"], ids["kreil"], "hunts")
    db.add_link(conn, ids["s1"], ids["kreil"], "features")
    return ids


def test_a_secret_death_stays_secret(conn, world):
    kreil = db.get_entity(conn, world["kreil"])
    deaths.mark(conn, kreil, "Poisoned at the feast.", party_knows=False)
    dead = db.get_entity(conn, world["kreil"])
    assert dead["deceased"] == "secret" and dead["death_note"] == "Poisoned at the feast."
    assert db.reveals(conn, None) == []  # nothing for the party to have learned


def test_a_witnessed_death_is_logged_as_a_reveal(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "Beheaded.", party_knows=True)
    logged = db.reveals(conn, None)
    assert len(logged) == 1 and logged[0]["field"] == "Death" and logged[0]["text"] == "Beheaded."


def test_revealing_a_secret_death_later(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "Poisoned.", party_knows=False)
    deaths.reveal(conn, db.get_entity(conn, world["kreil"]))
    assert db.get_entity(conn, world["kreil"])["deceased"] == "known"
    assert [r["text"] for r in db.reveals(conn, None)] == ["Poisoned."]


def test_undo(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "Oops.", party_knows=False)
    deaths.undo(conn, world["kreil"])
    assert db.get_entity(conn, world["kreil"])["deceased"] == ""


def test_the_bible_tells_the_model_who_is_dead_and_who_knows(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "Poisoned.", party_knows=False)
    text = context.system_prompt(conn)
    assert "Kreil · CANON · party knows · DECEASED (the party doesn't know)" in text
    assert "Death: Poisoned." in text


def test_the_ripple_reaches_linked_entries_but_not_pcs_or_logs(conn, world):
    assert {t["id"] for t in deaths.ripple_targets(conn, world["kreil"])} == {world["aide"], world["guild"]}


def test_the_ripple_proposes_per_linked_entry_with_the_chosen_model(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "Poisoned.", party_knows=False)
    backend = script(change("personality", "Loyal, and lost."), no_change())  # aide (#2), then guild (#3)
    deaths.start_ripple(conn, db.get_entity(conn, world["kreil"]), "haiku")
    assert [c["model"] for c in backend.calls] == [llm.MODEL_CHOICES["haiku"][0]] * 2
    assert "does NOT know of this death" in backend.calls[0]["prompt"] and "Poisoned." in backend.calls[0]["prompt"]
    aide = proposals.latest_open(conn, world["aide"])
    assert aide["cause_id"] == world["kreil"] and aide["request"]["title"] == "After Kreil's death"
    assert proposals.latest_open(conn, world["guild"]) is None  # "no change" closes itself
    states = {r["entity"]["name"]: r["state"] for r in deaths.ripple_status(conn, world["kreil"])}
    assert states == {"Aide": "to review", "Guild": "nothing to change"}


def test_the_ripple_defaults_to_sonnet(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "", party_knows=True)
    backend = script(no_change(), no_change())
    deaths.start_ripple(conn, db.get_entity(conn, world["kreil"]))
    assert backend.calls[0]["model"] == llm.MODEL_CHOICES["sonnet"][0]
    assert "The party knows of this death." in backend.calls[0]["prompt"]


def test_a_failed_ripple_call_shows_up(conn, world):
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "", party_knows=True)
    script(llm.LLMError("the loom snapped"), no_change())
    deaths.start_ripple(conn, db.get_entity(conn, world["kreil"]))
    states = {r["entity"]["name"]: (r["state"], r["error"]) for r in deaths.ripple_status(conn, world["kreil"])}
    assert states["Aide"] == ("failed", "the loom snapped")


def test_the_dead_need_no_voice(conn, world):
    assert world["kreil"] in {e["id"] for e in briefing.voiceless(conn)}
    deaths.mark(conn, db.get_entity(conn, world["kreil"]), "", party_knows=True)
    assert world["kreil"] not in {e["id"] for e in briefing.voiceless(conn)}


# ── Through the app ────────────────────────────────────────────────────────

@pytest.fixture
def ids(client):
    with closing(db.get_connection()) as conn:
        kreil = db.create_entity(conn, "npc", "Kreil", {}, known=True)
        aide = db.create_entity(conn, "npc", "Aide", {"personality": "Loyal."}, known=True)
        guild = db.create_entity(conn, "faction", "Guild", {})
        db.add_link(conn, aide, kreil, "serves")
        return {"kreil": kreil, "aide": aide, "guild": guild}


def test_mark_and_ripple_from_the_page(client, ids):
    page = client.get(f"/npc/{ids['kreil']}").text
    assert "Mark deceased…" in page and "the 1 linked entry" in page
    backend = script(change("personality", "Loyal, and adrift."))
    client.post(f"/npc/{ids['kreil']}/deceased", data={"how": "Fell from the tower.", "ripple": "1", "model": "sonnet"})
    assert len(backend.calls) == 1
    page = client.get(f"/npc/{ids['kreil']}").text
    assert "✝ Deceased" in page and "secret: the party doesn't know" in page and "to review" in page
    aide_page = client.get(f"/npc/{ids['aide']}").text
    assert "After Kreil&#39;s death: proposed changes" in aide_page or "After Kreil's death: proposed changes" in aide_page
    assert "After Kreil" in client.get("/briefing").text


def test_no_ripple_unless_asked(client, ids):
    backend = script()
    client.post(f"/npc/{ids['kreil']}/deceased", data={"how": "Quietly."})
    assert backend.calls == []


def test_the_party_sees_a_death_only_once_known(client, ids):
    client.post(f"/npc/{ids['kreil']}/deceased", data={"how": "Fell from the tower. ||Pushed by the aide.||"})
    client.get("/view/party")
    assert "Deceased" not in client.get(f"/npc/{ids['kreil']}").text
    client.get("/view/dm")
    client.post(f"/npc/{ids['kreil']}/deceased/reveal")
    client.get("/view/party")
    page = client.get(f"/npc/{ids['kreil']}").text
    assert "✝ Deceased" in page and "Fell from the tower." in page and "Pushed by the aide" not in page


def test_only_the_mortal_can_die(client, ids):
    assert client.post(f"/faction/{ids['guild']}/deceased", data={}).status_code == 404
    assert "Mark deceased" not in client.get(f"/faction/{ids['guild']}").text


def test_players_cannot_kill_anyone(player, ids):
    assert player.post(f"/npc/{ids['kreil']}/deceased", data={"how": "x"}).status_code == 403
