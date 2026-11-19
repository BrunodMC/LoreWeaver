from contextlib import closing

import pytest

from src.app import db, deaths, generation, llm, mentions


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


def make(conn, type_key, name, **fields):
    return db.create_entity(conn, type_key, name, fields, known=True)


def found(conn):
    return {(m.source["name"], tuple(t["name"] for t in m.targets)) for m in mentions.scan(conn)}


def test_a_full_name_in_the_text_is_an_unlinked_mention(conn):
    make(conn, "npc", "Kreil Dirn")
    make(conn, "npc", "Lord Vannis", notes="Often works with Kreil Dirn. ||He owes him.||")
    assert found(conn) == {("Lord Vannis", ("Kreil Dirn",))}


def test_a_link_in_either_direction_settles_it(conn):
    kreil = make(conn, "npc", "Kreil Dirn")
    vannis = make(conn, "npc", "Lord Vannis", notes="Works with Kreil Dirn.")
    db.add_link(conn, kreil, vannis, "uses")
    assert found(conn) == set()


def test_short_forms(conn):
    make(conn, "npc", "Kreil Dirn")
    make(conn, "npc", "Nikta, the Great Owl")
    make(conn, "location", "Stonehaven (Ostara)")
    make(conn, "arc", "The Spark")
    make(conn, "pc", "Tamsin", notes="Kreil hired her. Nikta watches. Born in Ostara. Carries the Spark.")
    assert found(conn) == {("Tamsin", ("Kreil Dirn",)), ("Tamsin", ("Nikta, the Great Owl",)),
                           ("Tamsin", ("Stonehaven (Ostara)",)), ("Tamsin", ("The Spark",))}


def test_the_longest_name_wins(conn):
    make(conn, "npc", "Lord Vannis")
    make(conn, "location", "Caer Vannis")
    make(conn, "pc", "Tamsin", notes="Slept at Caer Vannis.")
    assert found(conn) == {("Tamsin", ("Caer Vannis",))}


def test_titles_and_creature_names_are_not_names(conn):
    make(conn, "npc", "Lord Vannis")
    make(conn, "adversary", "Moth-dust larvae")
    make(conn, "pc", "Tamsin", notes="The Lord of the castle. A Moth-Eater strikes.")
    assert found(conn) == set()


def test_a_word_shared_by_two_people_is_not_a_name(conn):
    make(conn, "npc", "Soren Vale")
    make(conn, "npc", "Ida Vale")
    make(conn, "pc", "Tamsin", notes="Met a Vale once.")
    assert found(conn) == set()


def test_a_word_names_the_person_over_their_faction(conn):
    make(conn, "npc", "Archmage Phylax")
    make(conn, "faction", "Phylax's taskforce (unnamed)")
    make(conn, "pc", "Tamsin", notes="Phylax lied.")
    assert found(conn) == {("Tamsin", ("Archmage Phylax",))}


def test_shared_names_make_one_mention_with_a_choice(conn):
    make(conn, "arc", "The Spark")
    make(conn, "item", "The Spark")
    make(conn, "pc", "Tamsin", notes="Carries the Spark.")
    assert found(conn) == {("Tamsin", ("The Spark", "The Spark"))}


def test_provenance_lines_are_not_relations(conn):
    make(conn, "arc", "The Whisper at Sundown")
    make(conn, "location", "Eye-Stone", notes="||From The Whisper at Sundown: moths nest here.||")
    assert found(conn) == set()


def test_dismissing_a_pair_or_a_target(conn):
    haven = make(conn, "location", "Haven")
    kreil = make(conn, "npc", "Kreil Dirn")
    vannis = make(conn, "npc", "Lord Vannis", notes="Kreil Dirn's man in Haven.")
    make(conn, "pc", "Tamsin", notes="Haven-born.")
    mentions.dismiss(conn, vannis, kreil)
    assert found(conn) == {("Lord Vannis", ("Haven",)), ("Tamsin", ("Haven",))}
    mentions.dismiss(conn, None, haven)
    assert found(conn) == set()


def test_the_ripple_offers_mentions_unticked(conn):
    kreil = make(conn, "npc", "Kreil Dirn")
    aide = make(conn, "npc", "Aide")
    db.add_link(conn, aide, kreil, "serves")
    vannis = make(conn, "npc", "Lord Vannis", notes="Works with Kreil Dirn.")
    make(conn, "pc", "Tamsin", notes="Hunts Kreil.")  # PCs are never rippled
    candidates = {c["entity"]["id"]: c["linked"] for c in deaths.ripple_candidates(conn, kreil)}
    assert candidates == {aide: True, vannis: False}


def test_the_ripple_goes_only_where_ticked(conn):
    kreil = make(conn, "npc", "Kreil Dirn")
    aide = make(conn, "npc", "Aide")
    db.add_link(conn, aide, kreil, "serves")
    vannis = make(conn, "npc", "Lord Vannis", notes="Works with Kreil Dirn.")
    deaths.mark(conn, db.get_entity(conn, kreil), "", party_knows=False)
    nothing = {"summary": "", "fields": {}, "reasons": [], "proposed_links": [], "dm_notes": ""}
    backend = llm.FakeBackend([nothing])
    llm.set_backend(backend)
    assert deaths.start_ripple(conn, db.get_entity(conn, kreil), None, {vannis}) == [vannis]
    assert "Lord Vannis" in backend.calls[0]["prompt"].splitlines()[0]
    assert [r["entity"]["id"] for r in deaths.ripple_status(conn, kreil)] == [vannis]


# ── Through the app ────────────────────────────────────────────────────────

@pytest.fixture
def pair(client):
    with closing(db.get_connection()) as conn:
        kreil = make(conn, "npc", "Kreil Dirn")
        vannis = make(conn, "npc", "Lord Vannis", notes="Works with Kreil Dirn.")
        return kreil, vannis


def test_link_from_the_page(client, pair):
    kreil, vannis = pair
    page = client.get("/mentions").text
    assert "Works with Kreil Dirn." in page and "Never suggest Kreil Dirn" in page
    assert "/mentions" in client.get("/briefing").text
    client.post("/mentions/link", data={"source_id": vannis, "target_id": kreil, "label": "works with",
                                        "direction": "forward", "known": "1"})
    with closing(db.get_connection()) as conn:
        out = db.links_for(conn, vannis)["outgoing"]
        assert [(l["other_id"], l["relation_label"], l["known_to_party"]) for l in out] == [(kreil, "works with", 1)]
        assert mentions.scan(conn) == []


def test_reverse_direction_and_a_label_is_required(client, pair):
    kreil, vannis = pair
    assert client.post("/mentions/link", data={"source_id": vannis, "target_id": kreil, "label": " "}).status_code == 422
    client.post("/mentions/link", data={"source_id": vannis, "target_id": kreil, "label": "employs",
                                        "direction": "reverse"})
    with closing(db.get_connection()) as conn:
        assert db.links_for(conn, kreil)["outgoing"][0]["other_id"] == vannis


def test_dismiss_from_the_page(client, pair):
    kreil, vannis = pair
    client.post("/mentions/dismiss", data={"source_id": vannis, "target_id": kreil, "scope": "pair"})
    assert "Tidy." in client.get("/mentions").text


def test_the_death_form_offers_the_picker(client, pair):
    kreil, vannis = pair
    page = client.get(f"/npc/{kreil}").text
    assert "Propose consequences for" in page and "names them, unlinked" in page


def test_players_cannot_link_or_dismiss(player, pair):
    kreil, vannis = pair
    assert player.get("/mentions", follow_redirects=False).status_code == 303
    assert player.post("/mentions/link", data={"source_id": vannis, "target_id": kreil, "label": "x"}).status_code == 403
