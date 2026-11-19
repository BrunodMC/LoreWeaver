import json
from contextlib import closing
from datetime import date

import pytest

from src.app import briefing, db, generation, llm


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


def make(conn, type_key, name, fields=None, status="canon", known=False):
    return db.create_entity(conn, type_key, name, fields or {}, status=status, known=known)


def test_last_session_counts_the_days(conn):
    assert briefing.last_session(conn) is None
    make(conn, "session", "Session 1", {"session_date": "2026-10-01"})
    latest = make(conn, "session", "Session 2", {"session_date": "2026-11-01"})
    found = briefing.last_session(conn, today=date(2026, 11, 19))
    assert found["entity"]["id"] == latest and found["days_ago"] == 18


def test_a_session_without_a_date_still_shows(conn):
    make(conn, "session", "Undated")
    assert briefing.last_session(conn)["days_ago"] is None


def test_open_threads_put_the_ones_near_the_party_first(conn):
    pc = make(conn, "pc", "Tamsin", known=True)
    far = make(conn, "arc", "Aaa distant rumour", status="draft")
    near = make(conn, "arc", "Zzz debt to the guild", status="draft")
    ignored = make(conn, "arc", "Scratch idea", status="draft")  # a draft not flagged open
    for e in (far, near):
        db.set_ai_context(conn, e, True)
    db.add_link(conn, pc, near, "owes")
    threads = briefing.open_threads(conn, None)
    assert [t["entity"]["id"] for t in threads] == [near, far]
    assert threads[0]["near"] and not threads[1]["near"]
    assert ignored not in [t["entity"]["id"] for t in threads]


def test_the_last_session_also_counts_as_near(conn):
    session = make(conn, "session", "Session 2")
    thread = make(conn, "arc", "Who lit the beacon", status="draft")
    db.set_ai_context(conn, thread, True)
    db.add_link(conn, thread, session, "raised in")
    assert briefing.open_threads(conn, session)[0]["near"]


def test_voiceless_lists_only_met_canon_faces_with_no_voice(conn):
    session = make(conn, "session", "Session 1")
    pc = make(conn, "pc", "Tamsin", known=True)
    mute = make(conn, "npc", "Mute", known=True)
    voiced = make(conn, "npc", "Voiced", {"voice": "Gravel and pauses."}, known=True)
    stranger = make(conn, "npc", "Stranger")  # the party hasn't heard of them
    deity = make(conn, "npc", "Distant god", known=True)  # known OF, never met: not linked to a session or PC
    draft = make(conn, "npc", "Draft face", status="draft", known=True)
    beast = make(conn, "adversary", "Beast", {"voice": "  "}, known=True)
    for e in (mute, voiced, stranger, draft):
        db.add_link(conn, session, e, "features")
    db.add_link(conn, beast, pc, "mauled")
    assert {e["id"] for e in briefing.voiceless(conn)} == {mute, beast}
    assert deity not in {e["id"] for e in briefing.voiceless(conn)}


def test_a_new_session_entry_is_dated_today(client):
    assert f'value="{date.today().isoformat()}"' in client.get("/session/new").text


def test_open_audits_count_their_findings(conn):
    target = make(conn, "npc", "Kreil", known=True)
    with conn:
        conn.execute("INSERT INTO audits (kind, entity_id, findings) VALUES ('secrecy', ?, ?)",
                     (target, json.dumps([{"key": "a"}, {"key": "b"}])))
        conn.execute("INSERT INTO audits (kind, entity_id, findings, status) VALUES ('secrecy', ?, '[]', 'closed')",
                     (target,))
    audits = briefing.open_audits(conn)
    assert len(audits) == 1 and audits[0]["findings"] == 2 and audits[0]["name"] == "Kreil"


def test_the_page_gathers_the_desk(client):
    with closing(db.get_connection()) as conn:
        known = make(conn, "npc", "Known Face", known=True)
        db.add_link(conn, make(conn, "session", "Session 1"), known, "features")
        db.record_reveal(conn, db.get_entity(conn, known), "Hook", "They smuggle.")
    llm.set_backend(llm.FakeBackend([{
        "concept": "A pilot.", "name": "Ossa Grell", "fields": {"personality": "Wry."},
        "proposed_links": [], "open_threads_touched": [], "dm_notes": "",
    }]))
    generation.start(generation.GenerationRequest("npc", "a ferry pilot", []))
    page = client.get("/briefing")
    assert page.status_code == 200
    assert "1 AI draft to review" in page.text and "Ossa Grell" in page.text
    assert "1 unfiled reveal" in page.text
    assert "Known Face" in page.text  # no voice yet
    assert 'href="/briefing"' in page.text  # in the DM header


def test_an_empty_bible_briefs_calmly(client):
    page = client.get("/briefing")
    assert page.status_code == 200 and "No session entries yet" in page.text and "Clear." in page.text


def test_the_briefing_is_dm_only(client, player):
    assert player.get("/briefing", follow_redirects=False).status_code == 303
    client.get("/view/party")
    assert client.get("/briefing").status_code == 404
