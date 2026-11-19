"""Proposals: the AI suggests changes to an entry that already exists, and the GM picks which to apply.

Generation makes new drafts; this revises what's there. One call looks at one entry against the whole
bible and returns only the fields it would change (each rewritten in full, with a reason) plus any new
links. Nothing touches the entry until the GM ticks changes and applies them. Each changed field's text
at proposal time is kept, so a hand edit made in the meantime is caught instead of overwritten, and the
old text stays on record.

A proposal kind says which entries it fits, what it asks, and which model it uses by default. The GM
can pick the model per call: a consequence that nudges one line doesn't need the slowest model.

The first kind is "Time passes…": in-game time has gone by, so where someone is, what they're after
and how they stand may have moved on.
"""
import difflib
import html
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field

from markupsafe import Markup

from . import context, db, generation, knowledge, llm
from .entity_types import ENTITY_TYPES

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2


class ProposalFailed(Exception):
    pass


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    types: tuple[str, ...]
    default_model: str  # a key of llm.MODEL_CHOICES
    instructions: Callable[[dict], list[str]]  # (request) → the task, in lines


# ── The kinds ──────────────────────────────────────────────────────────────

def time_passes_instructions(request: dict) -> list[str]:
    lines = [f"In-game time has passed since this entry was written: {request.get('elapsed') or 'some time'}."]
    if request.get("events"):
        lines.append(f"What happened in the world meanwhile, per the GM: {request['events']}")
    lines += [
        "Bring the entry up to date: where they are now, what they're doing, how their plans, motives, standing "
        "and relationships have moved, given who they are, what they want, what the session log says the party "
        "did, and what the linked entries are up to. Change what time would plausibly change and leave the rest: "
        "a few weeks rarely changes much, a year can change a lot. Evolve the established facts; don't replace "
        "them with new ones that contradict the bible.",
    ]
    return lines


KINDS: dict[str, Kind] = {
    "time_passes": Kind("time_passes", "Time passes", ("npc", "faction", "arc", "location"), "opus",
                        time_passes_instructions),
}

COMMON_RULES = """\
How to answer:
- You are revising an existing entry, not drafting a new one. Return ONLY the fields you change; fields you \
leave out stay exactly as they are. An answer with no field changes is fine when nothing would change.
- Rewrite each changed field IN FULL: its whole new text, keeping every part that still holds, in the voice and \
density of the existing text. Never return a fragment or a diff.
- Keep existing ||secrets|| unless the change is about them. What the party wouldn't know goes in ||bars||; \
bars always close.
- Give one short reason per changed field.
- Propose a new link only for a relationship this change creates with an existing entry, and only if it isn't \
already linked.
- UNDECIDED entries are open questions: don't settle one silently. If your change leans on one, say so in \
dm_notes.
- The Notes field is the GM's and isn't yours to change."""


# ── The call ───────────────────────────────────────────────────────────────

def editable_fields(entity_type: str):
    return context.model_fields(ENTITY_TYPES[entity_type])


def schema(entity_type: str) -> dict:
    fields = context.output_schema(entity_type, partial=True)["properties"]["fields"]
    keys = [f.key for f in editable_fields(entity_type)]
    return {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "fields", "reasons", "proposed_links", "dm_notes"],
        "properties": {
            "summary": {"type": "string", "description": "One or two sentences: what changed for this entry, and why."},
            "fields": fields,
            "reasons": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["field", "why"],
                "properties": {"field": {"type": "string", "enum": keys}, "why": {"type": "string"}}}},
            "proposed_links": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["target_id", "direction", "label", "party_knows", "why"],
                "properties": {
                    "target_id": {"type": "integer", "description": "Id of an existing bible entry."},
                    "direction": {"type": "string", "enum": ["from_entry", "to_entry"],
                                  "description": "from_entry: <this entry> <label> <target>. to_entry: the reverse."},
                    "label": {"type": "string", "description": "Short relation, like 'now serves' or 'hunts'."},
                    "party_knows": {"type": "boolean"},
                    "why": {"type": "string"},
                }}},
            "dm_notes": {"type": "string", "description": "Anything the GM should know about these changes. GM only."},
        },
    }


def prompt(kind: Kind, entity: dict, request: dict) -> str:
    etype = ENTITY_TYPES[entity["type"]]
    current = {f.key: entity["fields"].get(f.key, "") for f in editable_fields(entity["type"])}
    lines = [
        f"Revise an existing {etype.label}: {context.ref(entity)}"
        + (" (the party knows this entry)" if db.visible_to_party(entity) else " (hidden from the party)"),
        "",
        *kind.instructions(request),
    ]
    if request.get("notes"):
        lines.append(f"The GM's notes: {request['notes']}")
    lines += ["", COMMON_RULES, "", "The entry as it stands (field key → text):",
              json.dumps(current, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


@dataclass
class Checked:
    summary: str
    changes: dict
    reasons: dict
    links: list
    dm_notes: str
    problems: list[str] = field(default_factory=list)


def check(data: dict, entity: dict, entities: list[dict], existing_links: set[tuple]) -> Checked:
    data = context._cleaned(data)
    allowed = {f.key: f for f in editable_fields(entity["type"])}
    problems, changes = [], {}
    for key, value in (data.get("fields") or {}).items():
        if key not in allowed:
            continue
        value = str(value).strip()
        f = allowed[key]
        if value == entity["fields"].get(key, "").strip():
            continue  # "changed" to what it already says
        if f.widget == "select" and value and value not in f.options:
            problems.append(f"{f.label} must be one of {', '.join(f.options)}, not {value!r}.")
        if f.widget == "number" and value and not value.isdigit():
            problems.append(f"{f.label} must be a whole number, not {value!r}.")
        if knowledge.has_unclosed_secret(value):
            problems.append(f"{f.label} has an unclosed || secret marker.")
        changes[key] = value
    reasons = {r.get("field"): str(r.get("why", "")).strip() for r in data.get("reasons") or []
               if r.get("field") in changes}

    by_id = {e["id"]: e for e in entities}
    links, seen = [], set()
    for link in data.get("proposed_links") or []:
        target = by_id.get(link.get("target_id"))
        label = " ".join(str(link.get("label", "")).split())
        if target is None or target["id"] == entity["id"]:
            problems.append(f"A proposed link points at entry #{link.get('target_id')}, which isn't another "
                            "entry in the bible.")
            continue
        if not label:
            problems.append(f"The proposed link to {context.ref(target)} has no label.")
            continue
        outgoing = link.get("direction") != "to_entry"
        pair = (entity["id"], target["id"]) if outgoing else (target["id"], entity["id"])
        if pair in existing_links or (pair, label.casefold()) in seen:
            continue
        seen.add((pair, label.casefold()))
        links.append({"from_id": pair[0], "to_id": pair[1], "target_id": target["id"], "target": target["name"],
                      "target_type": target["type"], "outgoing": outgoing, "label": label,
                      "party_knows": bool(link.get("party_knows")), "why": str(link.get("why", "")).strip()})
    return Checked(str(data.get("summary", "")).strip(), changes, reasons, links,
                   str(data.get("dm_notes", "")).strip(), problems)


def model_choice(kind: Kind, choice: str | None) -> str:
    return choice if choice in llm.MODEL_CHOICES else kind.default_model


def run(conn, kind_key: str, entity_id: int, request: dict, choice: str | None = None) -> int:
    """Ask for a proposal and store it (open). Returns its id."""
    kind = KINDS[kind_key]
    entity = db.get_entity(conn, entity_id)
    if entity is None or entity["type"] not in kind.types:
        raise ProposalFailed(f"{kind.label} doesn't apply to that entry.")
    entities = context.context_entities(conn)
    if entity["id"] not in {e["id"] for e in entities}:
        entities.append(entity)  # an unflagged draft: still show the model the entry it's revising
    rows = conn.execute("SELECT from_entity_id, to_entity_id FROM links").fetchall()
    existing = {(r[0], r[1]) for r in rows}
    system = context.system_prompt(conn, entities)
    ask = prompt(kind, entity, request)
    choice = model_choice(kind, choice)
    attempt = ask
    for _ in range(MAX_ATTEMPTS):
        result = llm.generate(system, attempt, schema(entity["type"]), model=llm.MODEL_CHOICES[choice][0])
        checked = check(result.data, entity, entities, existing)
        if not checked.problems:
            break
        attempt = context.retry_prompt(ask, checked.problems)
    else:
        raise ProposalFailed("The proposal still had problems after a retry:\n"
                             + "\n".join(f"- {p}" for p in checked.problems))
    base = {k: entity["fields"].get(k, "") for k in checked.changes}
    with conn:
        conn.execute("UPDATE proposals SET status = 'closed' WHERE entity_id = ? AND kind = ? AND status = 'open'",
                     (entity_id, kind_key))  # a fresh proposal replaces a stale one
        cur = conn.execute(
            "INSERT INTO proposals (kind, entity_id, request, summary, changes, base, reasons, links, dm_notes, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind_key, entity_id, json.dumps({**request, "model_choice": choice}), checked.summary,
             json.dumps(checked.changes), json.dumps(base), json.dumps(checked.reasons), json.dumps(checked.links),
             checked.dm_notes, result.model))
    return cur.lastrowid


# ── Storage and ruling ─────────────────────────────────────────────────────

JSON_COLUMNS = ("request", "changes", "base", "reasons", "links", "state")


def _from_row(row) -> dict:
    out = dict(row)
    for key in JSON_COLUMNS:
        out[key] = json.loads(out[key])
    return out


def get(conn, proposal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _from_row(row) if row else None


def latest_open(conn, entity_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM proposals WHERE entity_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                       (entity_id,)).fetchone()
    return _from_row(row) if row else None


def all_open(conn) -> list[dict]:
    rows = conn.execute("SELECT p.*, e.type AS entity_type, e.name AS entity_name FROM proposals p "
                        "JOIN entities e ON e.id = p.entity_id WHERE p.status = 'open' ORDER BY p.id DESC").fetchall()
    return [_from_row(r) for r in rows]


def apply(conn, proposal: dict, fields: set[str], links: set[int]) -> list[str]:
    """Apply the ticked field changes and links and close the proposal. A field edited by hand since the
    proposal was made is left alone and reported (as its key) rather than overwritten."""
    entity = db.get_entity(conn, proposal["entity_id"])
    state, conflicts, new_fields = {}, [], dict(entity["fields"])
    for key, text in proposal["changes"].items():
        if key not in fields:
            state[key] = "rejected"
        elif entity["fields"].get(key, "") != proposal["base"].get(key, ""):
            state[key] = "conflict"
            conflicts.append(key)
        else:
            new_fields[key] = text
            state[key] = "applied"
    if new_fields != entity["fields"]:
        db.update_entity(conn, entity["id"], entity["name"], new_fields)
    for i, link in enumerate(proposal["links"]):
        if i in links and db.get_entity(conn, link["target_id"]) is not None:
            db.add_link(conn, link["from_id"], link["to_id"], link["label"], link["party_knows"])
            state[f"link:{i}"] = "applied"
        else:
            state[f"link:{i}"] = "rejected"
    _close(conn, proposal["id"], state)
    return conflicts


def dismiss(conn, proposal: dict) -> None:
    _close(conn, proposal["id"], {k: "rejected" for k in proposal["changes"]})


def _close(conn, proposal_id: int, state: dict) -> None:
    with conn:
        conn.execute("UPDATE proposals SET status = 'closed', state = ? WHERE id = ?", (json.dumps(state), proposal_id))


# ── Showing a change ───────────────────────────────────────────────────────

_TOKENS = re.compile(r"\s+|[^\s]+")


def word_diff(old: str, new: str) -> Markup:
    """The new text with what was cut struck through and what was added marked, word by word."""
    a, b = _TOKENS.findall(old or ""), _TOKENS.findall(new or "")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
            continue
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
    return Markup("".join(out))


# ── Background tasks ───────────────────────────────────────────────────────

@dataclass
class Task:
    kind: str
    entity_id: int
    status: str = "running"  # running | done | failed
    error: str = ""
    model: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)


TASKS: dict[int, Task] = {}  # one at a time per entry
_lock = threading.Lock()


def _work(task: Task, request: dict, choice: str | None) -> None:
    try:
        with closing(db.get_connection()) as conn:
            run(conn, task.kind, task.entity_id, request, choice)
        task.status = "done"
    except (ProposalFailed, llm.LLMError) as exc:
        task.error, task.status = str(exc), "failed"
    except Exception as exc:  # a crash must still end the task, or the page polls forever
        log.exception("Proposal %s for #%s crashed", task.kind, task.entity_id)
        task.error, task.status = f"Unexpected error: {exc}", "failed"


def task_for(entity_id: int) -> Task | None:
    return TASKS.get(entity_id)


def start(kind: str, entity_id: int, request: dict, choice: str | None = None) -> Task:
    current = task_for(entity_id)
    if current and current.status == "running":
        return current
    task = Task(kind, entity_id, model=model_choice(KINDS[kind], choice))
    with _lock:
        TASKS[entity_id] = task
    if generation.INLINE:
        _work(task, request, choice)
    else:
        threading.Thread(target=_work, args=(task, request, choice), daemon=True,
                         name=f"proposal-{kind}-{entity_id}").start()
    return task
