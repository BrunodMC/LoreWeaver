import sqlite3
from datetime import datetime, timedelta

import pytest

from src.app import db


def test_migrations_apply_once_and_foreign_keys_are_on(conn):
    db.init_db()  # second run must be a no-op
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_update_bumps_updated_at(conn):
    entity_id = db.create_entity(conn, "npc", "Mara", {})
    conn.execute("UPDATE entities SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (entity_id,))
    conn.commit()
    db.update_entity(conn, entity_id, "Mara Voss", {})
    assert db.get_entity(conn, entity_id)["updated_at"] > "2000-01-01 00:00:00"

    conn.execute("UPDATE entities SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (entity_id,))
    conn.commit()
    db.set_status(conn, entity_id, "archived")
    assert db.get_entity(conn, entity_id)["updated_at"] > "2000-01-01 00:00:00"


def test_merge_fields_keeps_unknown_keys_and_drops_cleared_ones():
    merged = db.merge_fields(
        "npc",
        {"appearance": "  scarred  ", "hook": "   ", "not_a_field": "ignored"},
        {"hook": "old hook", "legacy_field": "kept"},
    )
    assert merged == {"appearance": "scarred", "legacy_field": "kept"}


def test_invalid_type_and_status_are_rejected(conn):
    with pytest.raises(ValueError):
        db.create_entity(conn, "dragon", "Smaug", {})
    with pytest.raises(ValueError):
        db.create_entity(conn, "npc", "Mara", {}, status="published")


def test_link_labels_are_normalized_and_deduped_case_insensitively(conn):
    a = db.create_entity(conn, "npc", "Mara", {})
    b = db.create_entity(conn, "faction", "Ashen Circle", {})
    assert db.add_link(conn, a, b, "  member   of ") is True
    assert db.add_link(conn, a, b, "Member Of") is False
    assert db.relation_labels(conn) == ["member of"]
    links = db.links_for(conn, b)
    assert links["outgoing"] == []
    assert [l["other_name"] for l in links["incoming"]] == ["Mara"]


def test_self_links_and_blank_labels_are_rejected(conn):
    a = db.create_entity(conn, "npc", "Mara", {})
    b = db.create_entity(conn, "npc", "Tobin", {})
    with pytest.raises(ValueError):
        db.add_link(conn, a, a, "knows")
    with pytest.raises(ValueError):
        db.add_link(conn, a, b, "   ")


def test_hard_delete_cascades_to_links(conn):
    a = db.create_entity(conn, "npc", "Mara", {})
    b = db.create_entity(conn, "faction", "Ashen Circle", {})
    db.add_link(conn, a, b, "member of")
    conn.execute("DELETE FROM entities WHERE id = ?", (a,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0


def test_links_to_missing_entities_fail(conn):
    a = db.create_entity(conn, "npc", "Mara", {})
    with pytest.raises(sqlite3.IntegrityError):
        db.add_link(conn, a, 9999, "knows")


class _Clock:
    current = datetime(2026, 1, 1)

    @classmethod
    def now(cls):
        cls.current += timedelta(seconds=1)
        return cls.current


def test_backups_skip_identical_snapshots_and_keep_newest_ten(conn, temp_db, monkeypatch):
    monkeypatch.setattr(db, "datetime", _Clock)
    backup_dir = temp_db.parent / "backups"

    first = db.backup_db()
    assert db.backup_db() == first
    assert len(list(backup_dir.glob("*.db"))) == 1

    for i in range(12):
        db.create_entity(conn, "npc", f"NPC {i}", {})
        newest = db.backup_db()

    backups = sorted(backup_dir.glob("*.db"))
    assert len(backups) == 10
    assert backups[-1] == newest


def test_lists_sort_numbers_naturally(conn):
    for name in ("Session 10", "Session 2", "session 1", "Session 0: Intro"):
        db.create_entity(conn, "npc", name, {})
    names = [e["name"] for e in db.list_entities(conn, "npc", ("canon",))]
    assert names == ["Session 0: Intro", "session 1", "Session 2", "Session 10"]


def test_sessions_sort_by_date_then_name_with_undated_last(conn):
    db.create_entity(conn, "session", "Session 10", {})
    db.create_entity(conn, "session", "Session 2", {})
    db.create_entity(conn, "session", "Late recap", {"session_date": "2026-03-01"})
    db.create_entity(conn, "session", "Early recap", {"session_date": "2025-11-20"})
    names = [e["name"] for e in db.list_entities(conn, "session", ("canon",))]
    assert names == ["Early recap", "Late recap", "Session 2", "Session 10"]


def test_backup_without_database_is_a_no_op(temp_db):
    assert db.backup_db() is None


def test_migration_upgrades_a_version_1_database(temp_db):
    v1 = (db.MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")
    with sqlite3.connect(temp_db) as raw:
        raw.executescript(v1 + "\nPRAGMA user_version = 1;")
        raw.execute("INSERT INTO entities (type, name) VALUES ('npc', 'Old Mara')")
    db.init_db()
    with sqlite3.connect(temp_db) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
        assert raw.execute("SELECT name, known_to_party FROM entities").fetchone() == ("Old Mara", 0)


def test_new_entities_and_links_default_to_unknown(conn):
    a = db.create_entity(conn, "npc", "Mara", {})
    b = db.create_entity(conn, "faction", "Ashen Circle", {})
    db.add_link(conn, a, b, "member of")
    assert db.get_entity(conn, a)["known_to_party"] == 0
    assert db.links_for(conn, a)["outgoing"][0]["known_to_party"] == 0


def test_party_view_filters_entities_and_links(conn):
    known = db.create_entity(conn, "npc", "Mara", {}, known=True)
    unknown = db.create_entity(conn, "npc", "Hidden", {})
    known_draft = db.create_entity(conn, "npc", "Drafty", {}, status="draft", known=True)
    faction = db.create_entity(conn, "faction", "Ashen Circle", {}, known=True)
    db.add_link(conn, known, faction, "member of", known=True)
    db.add_link(conn, known, faction, "secretly funds")
    db.add_link(conn, known, unknown, "knows", known=True)
    db.add_link(conn, known, known_draft, "knows", known=True)

    names = [e["name"] for e in db.list_entities(conn, "npc", ("canon", "draft"), party_view=True)]
    assert names == ["Mara"]
    outgoing = db.links_for(conn, known, party_view=True)["outgoing"]
    assert [(l["relation_label"], l["other_name"]) for l in outgoing] == [("member of", "Ashen Circle")]
    assert len(db.links_for(conn, known)["outgoing"]) == 4
    assert db.party_counts(conn)["npc"] == 1
