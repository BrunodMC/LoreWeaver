"""Audits: one call reads some text against the whole bible and flags passages for the GM to rule on.

An audit kind says what is checked (its subject), how to ask, how to check the answer and what
accepting a finding does. The GM always sees the findings as a yes/no list (obvious ones pre-ticked,
ambiguous ones not) and applies them with one button: nothing is changed unseen. Each kind picks its
own model; which model is good enough for which kind is for an eval to settle, not a guess.

The secrecy audit is the first kind: before an entry is revealed, find what in its player-visible
text would leak or imply something the bible keeps from the party.
"""
import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field

from . import context, db, generation, knowledge, llm
from .entity_types import ENTITY_TYPES

log = logging.getLogger(__name__)


class AuditFailed(Exception):
    pass


@dataclass
class Subject:
    entity: dict
    fields: dict[str, tuple[str, str]]  # key → (label, the text being audited)
    extra: list[str] = field(default_factory=list)  # context lines (e.g. links the party will see)


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    model: str | None  # None: the backend's default (the strong model)
    subject: Callable[..., Subject]               # (conn, entity) → what is audited
    prompt: Callable[[Subject], str]
    schema: Callable[[Subject], dict]
    check: Callable[[dict, Subject], tuple[list[dict], list[str]]]
    apply: Callable[..., None]                    # (conn, entity, finding) → change the bible


# ── Storage ────────────────────────────────────────────────────────────────

def _from_row(row) -> dict:
    found = dict(row)
    found["findings"], found["state"] = json.loads(found["findings"]), json.loads(found["state"])
    return found


def latest_open(conn, kind: str, entity_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM audits WHERE kind = ? AND entity_id = ? AND status = 'open' "
                       "ORDER BY id DESC LIMIT 1", (kind, entity_id)).fetchone()
    return _from_row(row) if row else None


def get(conn, audit_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    return _from_row(row) if row else None


def _close(conn, audit_id: int, state: dict) -> None:
    with conn:
        conn.execute("UPDATE audits SET status = 'closed', state = ? WHERE id = ?", (json.dumps(state), audit_id))


# ── Running one ────────────────────────────────────────────────────────────

def run(conn, kind_key: str, entity_id: int) -> int:
    """Audit an entry; stores and returns the audit (an empty finding list is a clean bill)."""
    kind = KINDS[kind_key]
    entity = db.get_entity(conn, entity_id)
    if entity is None:
        raise AuditFailed("That entry no longer exists.")
    subject = kind.subject(conn, entity)
    findings, model = [], None
    if subject.fields:
        system = context.system_prompt(conn)  # the whole bible, secrets included: leaks hide in implications
        prompt = kind.prompt(subject)
        attempt = prompt
        for _ in range(generation.MAX_ATTEMPTS):
            result = llm.generate(system, attempt, kind.schema(subject), model=kind.model)
            findings, problems = kind.check(result.data, subject)
            if not problems:
                break
            attempt = context.retry_prompt(prompt, problems)
        else:
            raise AuditFailed("The audit's answer still had problems after a retry:\n"
                              + "\n".join(f"- {p}" for p in problems))
        model = result.model
    for i, finding in enumerate(findings):
        finding["key"] = f"f{i}"
    with conn:
        cur = conn.execute("INSERT INTO audits (kind, entity_id, findings, model) VALUES (?, ?, ?, ?)",
                           (kind_key, entity_id, json.dumps(findings), model))
    return cur.lastrowid


def apply(conn, audit: dict, ticked: set[str]) -> list[str]:
    """Apply the ticked findings, reject the rest, and close the audit. Returns findings that couldn't
    be applied automatically (the GM fixes those by hand)."""
    kind = KINDS[audit["kind"]]
    state, failures = {}, []
    for finding in audit["findings"]:
        if finding["key"] not in ticked:
            state[finding["key"]] = "rejected"
            continue
        entity = db.get_entity(conn, audit["entity_id"])  # fresh each time: findings build on each other
        try:
            kind.apply(conn, entity, finding)
            state[finding["key"]] = "accepted"
        except knowledge.CannotBar as exc:
            state[finding["key"]] = "failed"
            failures.append(str(exc))
    _close(conn, audit["id"], state)
    return failures


def dismiss(conn, audit: dict) -> None:
    _close(conn, audit["id"], {f["key"]: "rejected" for f in audit["findings"]})


# ── Background tasks ───────────────────────────────────────────────────────

@dataclass
class Task:
    kind: str
    entity_id: int
    status: str = "running"  # running | done | failed
    error: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)


TASKS: dict[tuple[str, int], Task] = {}
_lock = threading.Lock()


def _work(task: Task) -> None:
    try:
        with closing(db.get_connection()) as conn:
            run(conn, task.kind, task.entity_id)
        task.status = "done"
    except (AuditFailed, llm.LLMError) as exc:
        task.error, task.status = str(exc), "failed"
    except Exception as exc:  # a crash must still end the task, or the page polls forever
        log.exception("Audit %s of #%s crashed", task.kind, task.entity_id)
        task.error, task.status = f"Unexpected error: {exc}", "failed"


def task_for(kind: str, entity_id: int) -> Task | None:
    return TASKS.get((kind, entity_id))


def start(kind: str, entity_id: int) -> Task:
    current = task_for(kind, entity_id)
    if current and current.status == "running":
        return current
    task = Task(kind, entity_id)
    with _lock:
        TASKS[(kind, entity_id)] = task
    if generation.INLINE:
        _work(task)
    else:
        threading.Thread(target=_work, args=(task,), daemon=True, name=f"audit-{kind}-{entity_id}").start()
    return task


# ── The secrecy audit ──────────────────────────────────────────────────────

SECRECY_MODEL = None  # the strong default: a missed leak is the costly error, and leaks are often implied

SECRECY_INSTRUCTIONS = """\
The GM is about to reveal the entry below to the players. What follows is exactly what they will see; \
the bible above holds everything they must not learn.

Find passages in this visible text that would leak, or let attentive players infer, something the \
bible keeps from them: any ||barred|| secret anywhere in the bible, an entry or link not marked \
"party knows", or a hidden identity, motive, allegiance or event. Leaks by implication count: a \
detail that only makes sense if you know the secret gives the secret away.

Don't flag what the party already knows (entries and links marked "party knows", and the unbarred \
text of entries they know). Don't flag mere flavour. An empty list is a good answer when nothing leaks.

For each leak: the field it's in, the passage quoted EXACTLY as it appears below (the shortest \
sentence or phrase that carries the leak), what it gives away, and a severity: "obvious" when it \
states or names the secret, "ambiguous" when it only hints, or the leak depends on a reading."""


def secrecy_subject(conn, entity: dict) -> Subject:
    etype = ENTITY_TYPES[entity["type"]]
    fields = {}
    for f in etype.fields:
        if f.dm_only:
            continue
        visible = knowledge.party_text(entity["fields"].get(f.key, ""))
        if visible:
            fields[f.key] = (f.label, visible)
    links = []
    for arrow, key in (("→", "outgoing"), ("←", "incoming")):
        for link in db.links_for(conn, entity["id"])[key]:
            other = db.get_entity(conn, link["other_id"])
            if link["known_to_party"] and other and db.visible_to_party(other):
                links.append(f"{arrow} {link['relation_label']} {other['name']}")
    return Subject(entity, fields, links)


def secrecy_prompt(subject: Subject) -> str:
    lines = [SECRECY_INSTRUCTIONS, "", f"Entry: {context.ref(subject.entity)}"]
    for key, (label, text) in subject.fields.items():
        lines += [f"[{key}] {label}:", text, ""]
    if subject.extra:
        lines += ["Links the party will see:", *subject.extra]
    return "\n".join(lines)


def secrecy_schema(subject: Subject) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["findings"],
        "properties": {"findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["field", "quote", "reason", "severity"],
            "properties": {
                "field": {"type": "string", "enum": list(subject.fields)},
                "quote": {"type": "string", "description": "Copied exactly from that field's visible text."},
                "reason": {"type": "string", "description": "What it gives away, and how."},
                "severity": {"type": "string", "enum": ["obvious", "ambiguous"]},
            }}}},
    }


def secrecy_check(data: dict, subject: Subject) -> tuple[list[dict], list[str]]:
    findings, problems, seen = [], [], set()
    for raw in data.get("findings") or []:
        key, quote = raw.get("field"), context.clean(raw.get("quote", ""))
        if key not in subject.fields:
            problems.append(f"Finding in unknown field {key!r}.")
            continue
        label, visible = subject.fields[key]
        if not quote or quote not in visible:
            problems.append(f"The quote {quote!r} isn't copied exactly from {label}. Quote it verbatim.")
            continue
        if (key, quote) in seen:
            continue
        seen.add((key, quote))
        severity = raw.get("severity") if raw.get("severity") in ("obvious", "ambiguous") else "ambiguous"
        findings.append({"field": key, "field_label": label, "quote": quote, "severity": severity,
                         "reason": context.clean(raw.get("reason", ""))})
    return findings, problems


def secrecy_apply(conn, entity: dict, finding: dict) -> None:
    fields = dict(entity["fields"])
    fields[finding["field"]] = knowledge.bar_quote(fields.get(finding["field"], ""), finding["quote"])
    db.update_entity(conn, entity["id"], entity["name"], fields)


KINDS: dict[str, Kind] = {
    "secrecy": Kind("secrecy", "Secrecy check", SECRECY_MODEL, secrecy_subject, secrecy_prompt, secrecy_schema,
                    secrecy_check, secrecy_apply),
}
