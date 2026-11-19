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


def by_entry(answers):
    """A fake answer chosen by which entry the prompt revises, so parallel order doesn't matter."""
    def respond(system, prompt, schema):
        first = prompt.splitlines()[0]
        for name, answer in answers.items():
            if f"] {name}" in first:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected prompt: {first}")
    return respond


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
    answers = by_entry({"Aide": change("personality", "Loyal, and lost."), "Guild": no_change()})
    backend = script(answers, answers)
    deaths.start_ripple(conn, db.get_entity(conn, world["kreil"]), "haiku")
    assert [c["model"] for c in backend.calls] == [llm.MODEL_CHOICES["haiku"][0]] * 2
    assert all("does NOT know of this death" in c["prompt"] and "Poisoned." in c["prompt"] for c in backend.calls)
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
    answers = by_entry({"Aide": llm.LLMError("the loom snapped"), "Guild": no_change()})
    script(answers, answers)
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
    assert "Mark deceased…" in page and "Propose consequences for" in page
    backend = script(change("personality", "Loyal, and adrift."))
    client.post(f"/npc/{ids['kreil']}/deceased", data={"how": "Fell from the tower.", "target": [str(ids["aide"])],
                                                       "model": "sonnet"})
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


# ── Individuals and types ──────────────────────────────────────────────────

def test_only_an_individual_adversary_without_a_person_can_die(conn):
    druid = db.create_entity(conn, "adversary", "Earth Druid", {})
    soldier = db.create_entity(conn, "adversary", "Soldier", {"individual": "A type"})
    brood = db.create_entity(conn, "adversary", "The brood-mother", {"individual": "One individual"})
    wraith_npc = db.create_entity(conn, "npc", "The Fanewraith", {})
    wraith = db.create_entity(conn, "adversary", "The Fanewraith", {"individual": "One individual"})
    db.add_link(conn, wraith, wraith_npc, "stat block of")
    named = db.create_entity(conn, "adversary", "Soren, armed", {"individual": "One individual"})
    soren = db.create_entity(conn, "npc", "Soren Maelir", {})
    db.add_link(conn, soren, named, "fights with the stat block")  # a stat-block relation, different names
    get = lambda i: db.get_entity(conn, i)
    assert [deaths.mortality(conn, get(i)) for i in (druid, soldier, brood, wraith, named, wraith_npc)] == \
        ["type", "type", "mortal", "person", "person", "mortal"]
    assert deaths.person_of(conn, get(wraith))["id"] == wraith_npc


def test_the_page_follows_mortality(client):
    with closing(db.get_connection()) as conn:
        druid = db.create_entity(conn, "adversary", "Earth Druid", {})
        brood = db.create_entity(conn, "adversary", "The brood-mother", {"individual": "One individual"})
        person = db.create_entity(conn, "npc", "The Fanewraith", {})
        wraith = db.create_entity(conn, "adversary", "The Fanewraith", {"individual": "One individual"})
        db.add_link(conn, wraith, person, "stat block of")
    assert "Mark deceased" not in client.get(f"/adversary/{druid}").text
    assert client.post(f"/adversary/{druid}/deceased", data={}).status_code == 409
    assert "Mark deceased" in client.get(f"/adversary/{brood}").text
    page = client.get(f"/adversary/{wraith}").text
    assert "Mark deceased" not in page and "The stat block of" in page and "kept on that page" in page
    client.post(f"/npc/{person}/deceased", data={"how": "Fell.", "party_knows": "1"})
    assert "✝ deceased" in client.get(f"/adversary/{wraith}").text


def test_the_generator_is_asked_individual_or_type():
    from src.app import context
    prop = context.output_schema("adversary")["properties"]["fields"]["properties"]["individual"]
    assert prop["enum"] == ["A type", "One individual"] and "named being" in prop["description"]
