"""The Briefing: the night before a session, everything that wants the GM's attention, on one page.

Read-only. It gathers what the app already tracks (drafts under review, unfiled reveals, open secrecy
checks, the threads still flagged UNDECIDED, the faces the party has met but the GM can't voice yet)
and links each item to where it gets handled. Nothing here decides anything.
"""
import json
import sqlite3
from datetime import date

from . import composite, db, generation, proposals

VOICED_TYPES = ("npc", "adversary")


def last_session(conn: sqlite3.Connection, today: date | None = None) -> dict | None:
    sessions = db.sessions_newest_first(conn)
    if not sessions:
        return None
    latest = sessions[0]
    days = None
    try:
        played = date.fromisoformat(latest["fields"].get("session_date", ""))
        days = ((today or date.today()) - played).days
    except ValueError:
        pass
    return {"entity": latest, "days_ago": days}


def _neighbours(conn: sqlite3.Connection, ids: set[int]) -> set[int]:
    """Everything one link away from these entries, in either direction."""
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT from_entity_id, to_entity_id FROM links "
                        f"WHERE from_entity_id IN ({marks}) OR to_entity_id IN ({marks})", (*ids, *ids)).fetchall()
    return {i for r in rows for i in r} - ids


def open_threads(conn: sqlite3.Connection, latest_session_id: int | None) -> list[dict]:
    """Drafts the GM flagged as open (included in AI context, so the model sees them as UNDECIDED).
    Those one link from a PC or the latest session come first: they're the ones likely to come up."""
    rows = conn.execute("SELECT * FROM entities WHERE status = 'draft' AND ai_context = 1").fetchall()
    threads = [db.entity_from_row(r) for r in rows]
    pcs = {r[0] for r in conn.execute("SELECT id FROM entities WHERE type = 'pc' AND status != 'archived'")}
    anchors = pcs | ({latest_session_id} if latest_session_id else set())
    near = _neighbours(conn, anchors)
    out = [{"entity": e, "near": e["id"] in near} for e in threads]
    return sorted(out, key=lambda t: (not t["near"], db.natural_key(t["entity"]["name"])))


def voiceless(conn: sqlite3.Connection) -> list[dict]:
    """Canon faces the party has actually met, with nothing written on how to play them. Met means
    linked to a session entry (it appeared in one) or straight to a PC: knowing OF someone (a god,
    a distant archmage) doesn't put them across the table."""
    marks = ",".join("?" * len(VOICED_TYPES))
    rows = conn.execute(f"SELECT * FROM entities WHERE type IN ({marks}) AND status = 'canon' "
                        f"AND known_to_party = 1 AND deceased = ''", VOICED_TYPES).fetchall()
    faces = [db.entity_from_row(r) for r in rows]
    meeting_places = {r[0] for r in conn.execute(
        "SELECT id FROM entities WHERE type IN ('session', 'pc') AND status != 'archived'")}
    met = _neighbours(conn, meeting_places)
    faces = [e for e in faces if e["id"] in met and not e["fields"].get("voice", "").strip()]
    return sorted(faces, key=lambda e: (e["type"], db.natural_key(e["name"])))


def open_audits(conn: sqlite3.Connection) -> list[dict]:
    """Secrecy checks (and future audits) that came back with findings nobody has ruled on yet."""
    rows = conn.execute("SELECT a.id, a.kind, a.findings, a.created_at, e.id AS entity_id, e.type, e.name "
                        "FROM audits a JOIN entities e ON e.id = a.entity_id "
                        "WHERE a.status = 'open' ORDER BY a.id DESC").fetchall()
    return [{**dict(r), "findings": len(json.loads(r["findings"]))} for r in rows]


def gather(conn: sqlite3.Connection, today: date | None = None) -> dict:
    session = last_session(conn, today)
    drafts = generation.pending_drafts(conn)
    brief = {
        "session": session,
        "drafts": drafts,
        "decisions": sum(d["review"]["open_decisions"] for d in drafts),
        "unfiled": db.reveals(conn, None),
        "composing": composite.workspaces(conn),
        "audits": open_audits(conn),
        "proposals": proposals.all_open(conn),
        "threads": open_threads(conn, session["entity"]["id"] if session else None),
        "voiceless": voiceless(conn),
    }
    brief["desk"] = sum(len(brief[k]) for k in ("drafts", "unfiled", "composing", "audits", "proposals"))
    return brief
