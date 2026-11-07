import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .entity_types import ENTITY_TYPES, STATUSES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BACKUPS_TO_KEEP = 10


def db_path() -> Path:
    return Path(os.environ.get("LOREWEAVER_DB", PROJECT_ROOT / "data" / "loreweaver.db"))


def get_connection() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread off: FastAPI may open the connection in one worker thread and use it
    # in another, but each connection still serves a single request.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite; cascades depend on it
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ── Migrations ─────────────────────────────────────────────────────────────

def _migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = re.match(r"(\d+)_", path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def init_db() -> None:
    with closing(get_connection()) as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, path in _migrations():
            if number <= current:
                continue
            script = path.read_text(encoding="utf-8")
            try:
                conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise


# ── Backups ────────────────────────────────────────────────────────────────

def backup_db(keep: int = BACKUPS_TO_KEEP) -> Path | None:
    """Snapshot the DB into data/backups/, skipping snapshots identical to the newest one."""
    source = db_path()
    if not source.exists():
        return None
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(backup_dir.glob(f"{source.stem}-*.db"))

    dest = backup_dir / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
    if dest.exists():
        return dest
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)

    if existing and existing[-1].read_bytes() == dest.read_bytes():
        dest.unlink()
        return existing[-1]

    for old in sorted(backup_dir.glob(f"{source.stem}-*.db"))[:-keep]:
        old.unlink()
    return dest


# ── Reads ──────────────────────────────────────────────────────────────────

def _to_dict(row: sqlite3.Row) -> dict:
    entity = dict(row)
    entity["fields"] = json.loads(entity["fields"])
    return entity


def get_entity(conn: sqlite3.Connection, entity_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    return _to_dict(row) if row else None


def visible_to_party(entity: dict) -> bool:
    return entity["status"] == "canon" and bool(entity["known_to_party"])


def list_entities(conn: sqlite3.Connection, entity_type: str, statuses: tuple[str, ...],
                  party_view: bool = False) -> list[dict]:
    if party_view:
        statuses = ("canon",)
    placeholders = ",".join("?" * len(statuses))
    party_filter = " AND known_to_party = 1" if party_view else ""
    rows = conn.execute(
        f"SELECT * FROM entities WHERE type = ? AND status IN ({placeholders}){party_filter} "
        "ORDER BY name COLLATE NOCASE",
        (entity_type, *statuses),
    ).fetchall()
    return [_to_dict(r) for r in rows]


def link_targets(conn: sqlite3.Connection, exclude_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, type, name, status FROM entities "
        "WHERE status != 'archived' AND id != ? ORDER BY type, name COLLATE NOCASE",
        (exclude_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def links_for(conn: sqlite3.Connection, entity_id: int, party_view: bool = False) -> dict[str, list[dict]]:
    """Party view only shows known links whose other end is itself visible to the party."""
    party_filter = (
        " AND l.known_to_party = 1 AND e.known_to_party = 1 AND e.status = 'canon'" if party_view else ""
    )
    columns = (
        "SELECT l.id, l.relation_label, l.known_to_party, e.id AS other_id, e.type AS other_type, "
        "e.name AS other_name, e.status AS other_status, e.known_to_party AS other_known "
    )
    outgoing = conn.execute(
        columns + "FROM links l JOIN entities e ON e.id = l.to_entity_id "
        f"WHERE l.from_entity_id = ?{party_filter} ORDER BY l.relation_label, e.name",
        (entity_id,),
    ).fetchall()
    incoming = conn.execute(
        columns + "FROM links l JOIN entities e ON e.id = l.from_entity_id "
        f"WHERE l.to_entity_id = ?{party_filter} ORDER BY l.relation_label, e.name",
        (entity_id,),
    ).fetchall()
    return {"outgoing": [dict(r) for r in outgoing], "incoming": [dict(r) for r in incoming]}


def get_link(conn: sqlite3.Connection, link_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
    return dict(row) if row else None


def relation_labels(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT relation_label FROM links ORDER BY relation_label COLLATE NOCASE"
    ).fetchall()
    return [r[0] for r in rows]


def status_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts = {t: {s: 0 for s in STATUSES} for t in ENTITY_TYPES}
    for row in conn.execute("SELECT type, status, COUNT(*) FROM entities GROUP BY type, status"):
        if row[0] in counts:
            counts[row[0]][row[1]] = row[2]
    return counts


def party_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {t: 0 for t in ENTITY_TYPES}
    for row in conn.execute(
        "SELECT type, COUNT(*) FROM entities WHERE status = 'canon' AND known_to_party = 1 GROUP BY type"
    ):
        if row[0] in counts:
            counts[row[0]] = row[1]
    return counts


# ── Writes (the only paths that modify data, so updated_at can't be skipped) ─

def merge_fields(entity_type: str, submitted: dict[str, str], existing: dict | None = None) -> dict:
    """Apply submitted spec fields over existing ones; keys no longer in the spec survive."""
    merged = dict(existing or {})
    for field in ENTITY_TYPES[entity_type].fields:
        value = (submitted.get(field.key) or "").strip()
        if value:
            merged[field.key] = value
        else:
            merged.pop(field.key, None)
    return merged


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")


def create_entity(conn: sqlite3.Connection, entity_type: str, name: str,
                  fields: dict, status: str = "canon", known: bool = False) -> int:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unknown entity type: {entity_type}")
    _check_status(status)
    with conn:
        cur = conn.execute(
            "INSERT INTO entities (type, name, status, fields, known_to_party) VALUES (?, ?, ?, ?, ?)",
            (entity_type, name.strip(), status, json.dumps(fields), int(known)),
        )
    return cur.lastrowid


def update_entity(conn: sqlite3.Connection, entity_id: int, name: str, fields: dict) -> None:
    with conn:
        conn.execute(
            "UPDATE entities SET name = ?, fields = ?, updated_at = datetime('now') WHERE id = ?",
            (name.strip(), json.dumps(fields), entity_id),
        )


def set_status(conn: sqlite3.Connection, entity_id: int, status: str) -> None:
    _check_status(status)
    with conn:
        conn.execute(
            "UPDATE entities SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, entity_id),
        )


def set_known(conn: sqlite3.Connection, entity_id: int, known: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE entities SET known_to_party = ?, updated_at = datetime('now') WHERE id = ?",
            (int(known), entity_id),
        )


def add_link(conn: sqlite3.Connection, from_id: int, to_id: int, label: str, known: bool = False) -> bool:
    """Returns False if an equivalent link (case-insensitive label) already exists."""
    label = " ".join(label.split())
    if not label:
        raise ValueError("Relation label is required")
    if from_id == to_id:
        raise ValueError("An entity can't link to itself")
    try:
        with conn:
            conn.execute(
                "INSERT INTO links (from_entity_id, to_entity_id, relation_label, known_to_party) "
                "VALUES (?, ?, ?, ?)",
                (from_id, to_id, label, int(known)),
            )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            return False
        raise
    return True


def set_link_known(conn: sqlite3.Connection, link_id: int, known: bool) -> None:
    with conn:
        conn.execute("UPDATE links SET known_to_party = ? WHERE id = ?", (int(known), link_id))


def remove_link(conn: sqlite3.Connection, link_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
