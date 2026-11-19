"""Deaths: mark someone deceased, and let the bible feel it.

Marking a death is light on the entry itself (a flag, whether the party knows, a line on how), but it
ripples: everyone and everything linked to the dead may be changed by it. The ripple asks one proposal
per linked entry (in parallel, a few at a time), each landing as an ordinary proposal for the GM to rule
on. Like every secret here, a death the party didn't witness stays hidden from them until revealed.
"""
import sqlite3

from . import db, mentions, proposals

DEATH_TYPES = ("npc", "adversary", "pc")
RIPPLE_TYPES = ("npc", "faction", "arc", "location", "item", "adversary")  # not PCs (players own them) or logs
RIPPLE_MODEL = "sonnet"  # many small calls: the strong model would be slow and heavy for nudges


def mark(conn: sqlite3.Connection, entity: dict, how: str, party_knows: bool) -> None:
    state = "known" if party_knows else "secret"
    with conn:
        conn.execute("UPDATE entities SET deceased = ?, death_note = ?, updated_at = datetime('now') WHERE id = ?",
                     (state, how.strip(), entity["id"]))
    if party_knows and db.visible_to_party(entity):
        db.record_reveal(conn, entity, "Death", how.strip() or f"{entity['name']} died.")


def reveal(conn: sqlite3.Connection, entity: dict) -> None:
    """The party learns of a death that was secret; logged for the session like any reveal."""
    if entity.get("deceased") != "secret":
        return
    with conn:
        conn.execute("UPDATE entities SET deceased = 'known', updated_at = datetime('now') WHERE id = ?",
                     (entity["id"],))
    if db.visible_to_party(entity):
        db.record_reveal(conn, entity, "Death", entity.get("death_note") or f"{entity['name']} died.")


def undo(conn: sqlite3.Connection, entity_id: int) -> None:
    """Back among the living (a mistake, or a resurrection). Proposals already applied stay applied."""
    with conn:
        conn.execute("UPDATE entities SET deceased = '', death_note = '', updated_at = datetime('now') WHERE id = ?",
                     (entity_id,))


def ripple_targets(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    """Entries one link from the dead (either direction), of the kinds a death can change."""
    links = db.links_for(conn, entity_id)
    ids = {link["other_id"] for key in ("outgoing", "incoming") for link in links[key]}
    targets = [db.get_entity(conn, i) for i in sorted(ids)]
    return [t for t in targets if t and t["type"] in RIPPLE_TYPES and t["status"] != "archived"]


def ripple_request(entity: dict, how: str, party_knows: bool) -> dict:
    return {"cause_id": entity["id"], "cause": f"[{entity['type']} #{entity['id']}] {entity['name']}",
            "how": how.strip(), "party_knows": party_knows, "title": f"After {entity['name']}'s death"}


def ripple_candidates(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    """Who a death could reach: linked entries (ticked by default), then entries that name the dead, or
    that the dead's entry names, without a link (unticked: a mention isn't always a relationship)."""
    links = db.links_for(conn, entity_id)
    out, seen = [], {entity_id}
    for key, arrow in (("outgoing", "→"), ("incoming", "←")):
        for link in links[key]:
            other = db.get_entity(conn, link["other_id"])
            if other and other["id"] not in seen and other["type"] in RIPPLE_TYPES and other["status"] != "archived":
                seen.add(other["id"])
                out.append({"entity": other, "linked": True, "why": f"{arrow} {link['relation_label']}"})
    for m in mentions.involving(conn, entity_id):
        others = m.targets if m.source["id"] == entity_id else [m.source]
        for other in others:
            if other["id"] not in seen and other["type"] in RIPPLE_TYPES and other["status"] != "archived":
                seen.add(other["id"])
                where = "named here" if m.source["id"] == entity_id else "names them"
                out.append({"entity": other, "linked": False, "why": f"{where}, unlinked: “{m.snippet}”"})
    return out


def start_ripple(conn: sqlite3.Connection, entity: dict, choice: str | None = None,
                 target_ids: set[int] | None = None) -> list[int]:
    """Ripple to the chosen candidates (by default, the linked ones)."""
    request = ripple_request(entity, entity.get("death_note", ""), entity.get("deceased") == "known")
    candidates = ripple_candidates(conn, entity["id"])
    chosen = [c["entity"] for c in candidates
              if (c["entity"]["id"] in target_ids if target_ids is not None else c["linked"])]
    for target in chosen:
        proposals.start("death_ripple", target["id"], request, choice)
    return [t["id"] for t in chosen]


def ripple_status(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    """For the dead entry's page: each linked entry the ripple reached, and where its proposal stands."""
    rows = conn.execute("SELECT p.id, p.entity_id, p.status, p.state, p.changes, p.links FROM proposals p "
                        "WHERE p.cause_id = ? ORDER BY p.id", (entity_id,)).fetchall()
    latest = {r["entity_id"]: r for r in rows}
    asked = set(latest) | {t.entity_id for t in proposals.TASKS.values()
                           if t.kind == "death_ripple" and t.cause_id == entity_id}
    out = []
    for target_id in sorted(asked):
        target = db.get_entity(conn, target_id)
        if target is None:
            continue
        task = proposals.task_for(target_id)
        task = task if task and task.cause_id == entity_id else None
        row = latest.get(target_id)
        if task and task.status in ("running", "failed") and (row is None or task.status == "running"):
            state = task.status
        elif row is None:
            continue
        elif row["changes"] == "{}" and row["links"] == "[]":
            state = "nothing to change"
        elif row["status"] == "open":
            state = "to review"
        else:
            state = "applied" if '"applied"' in row["state"] else "dismissed"
        out.append({"entity": target, "state": state, "error": task.error if task and state == "failed" else ""})
    return out
