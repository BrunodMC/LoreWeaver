import pytest

from src.app import db, search


def make(conn, type_key, name, status="canon", known=False, **fields):
    return db.create_entity(conn, type_key, name, fields, status, known)


def names(results):
    return [r.entity["name"] for r in results]


ALL = ("draft", "canon", "archived")


# ── Query parsing and folding ──────────────────────────────────────────────

def test_parse_query_keeps_quoted_phrases_and_dedupes():
    assert search.parse_query('lady\'s "Crimson   Veil" LADY\'S') == ["lady's", "crimson veil"]
    assert search.parse_query('  "" ') == []


def test_fold_ignores_case_and_accents():
    assert search.fold("Fané ÉCLAT Straße") == "fane eclat strasse"


# ── Matching ───────────────────────────────────────────────────────────────

def test_every_term_must_match_somewhere(conn):
    make(conn, "npc", "Phylax", notes="Stole the Reaping Eye.")
    make(conn, "npc", "Nikta", notes="Lost her eye.")
    assert names(search.search(conn, "phylax eye", ALL)) == ["Phylax"]
    assert names(search.search(conn, "eye", ALL)) == ["Nikta", "Phylax"]
    assert search.search(conn, "phylax dragon", ALL) == []


def test_accent_insensitive_match_and_highlight(conn):
    make(conn, "faction", "The Fané", goals="Guard the wilds.")
    [result] = search.search(conn, "fane", ALL)
    assert "<mark>Fané</mark>" in result.name_html


def test_phrase_matches_only_as_a_unit(conn):
    make(conn, "item", "Bloom", description="A crimson lady's veil flower.")
    make(conn, "item", "Cloak", description="A veil, crimson and torn.")
    assert names(search.search(conn, '"crimson lady\'s veil"', ALL)) == ["Bloom"]


def test_name_matches_rank_first(conn):
    make(conn, "location", "Haven", notes="A town.")
    make(conn, "npc", "Mara", notes="Lives in Haven.")
    make(conn, "npc", "Old Haven Road", notes="")
    assert names(search.search(conn, "haven", ALL))[0] == "Haven"
    assert names(search.search(conn, "haven", ALL))[-1] == "Mara"


def test_status_and_type_filters(conn):
    make(conn, "npc", "Ash", status="draft", notes="ember")
    make(conn, "npc", "Birch", status="archived", notes="ember")
    make(conn, "location", "Cinder Vale", notes="ember")
    assert names(search.search(conn, "ember", ("canon", "draft"))) == ["Ash", "Cinder Vale"]
    assert names(search.search(conn, "ember", ALL, type_key="npc")) == ["Ash", "Birch"]


def test_snippet_shows_secrets_styled_in_dm_view(conn):
    make(conn, "npc", "Phylax", notes="A warden. ||Stole the Reaping Eye.|| Smiles a lot.")
    [result] = search.search(conn, "reaping", ALL)
    label, html = result.snippets[0]
    assert label == "Notes"
    assert '<span class="secret"' in html and "<mark>Reaping</mark>" in html


def test_snippet_escapes_html(conn):
    make(conn, "npc", "Xi", notes="<script>alert(1)</script> poison")
    [result] = search.search(conn, "poison", ALL)
    assert "<script>" not in result.snippets[0][1]


def test_long_text_snippet_is_windowed(conn):
    make(conn, "arc", "Long", summary="word " * 100 + "needle " + "word " * 100)
    [result] = search.search(conn, "needle", ALL)
    html = result.snippets[0][1]
    assert html.startswith("… ") and html.endswith(" …") and len(html) < 400


def test_extra_fields_searchable_by_dm(conn):
    make(conn, "npc", "Old", legacy_field="forgotten lore")
    assert names(search.search(conn, "forgotten", ALL)) == ["Old"]
    assert search.search(conn, "forgotten", ALL, party_view=True) == []


# ── Party view must not leak ───────────────────────────────────────────────

@pytest.mark.parametrize("setup", [
    dict(status="canon", known=True, notes="Public. ||the hidden truth||"),  # secret text
    dict(status="canon", known=False, notes="the hidden truth"),  # unknown entity
    dict(status="draft", known=True, notes="the hidden truth"),  # draft
])
def test_party_search_never_matches_hidden_text(conn, setup):
    make(conn, "npc", "Subject", **setup)
    assert search.search(conn, "hidden", ALL, party_view=True) == []
    assert names(search.search(conn, "hidden", ALL)) == ["Subject"]


def test_party_search_skips_dm_only_fields(conn):
    make(conn, "adversary", "Thorn Beast", known=True, features="Regenerates", description="Thorny.")
    assert search.search(conn, "regenerates", ALL, party_view=True) == []
    assert names(search.search(conn, "thorny", ALL, party_view=True)) == ["Thorn Beast"]


def test_party_search_terms_cannot_straddle_secrets(conn):
    """Each term must appear in visible text; a term found only inside a secret doesn't count."""
    make(conn, "npc", "Phylax", known=True, notes="A warden. ||thief||")
    assert search.search(conn, "warden thief", ALL, party_view=True) == []


# ── Routes ─────────────────────────────────────────────────────────────────

def test_search_page_and_header_box(client, conn):
    make(conn, "npc", "Losgann", notes="A ranger.")
    page = client.get("/search", params={"q": "ranger"})
    assert page.status_code == 200
    assert "Losgann" in page.text and "<mark>ranger</mark>" in page.text
    assert 'action="/search"' in client.get("/").text


def test_search_route_not_swallowed_by_type_route(client):
    assert client.get("/search").status_code == 200


def test_search_route_party_view(client, conn):
    make(conn, "npc", "Lucenzo", known=True, notes="Bard. ||Owes the guild||")
    client.get("/view/party", follow_redirects=False)
    assert "Lucenzo" in client.get("/search", params={"q": "bard"}).text
    page = client.get("/search", params={"q": "guild"}).text
    assert "Lucenzo" not in page and "Owes" not in page


def test_bad_filters_fall_back(client, conn):
    make(conn, "npc", "Angela", notes="cleric")
    page = client.get("/search", params={"q": "cleric", "type": "dragon", "status": "bogus"})
    assert page.status_code == 200 and "Angela" in page.text
