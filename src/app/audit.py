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
        except (knowledge.CannotBar, knowledge.PassageGone) as exc:
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
    if subject.entity.get("party_name"):
        lines.append(f"The party knows this entry only as “{subject.entity['party_name']}”: its real name is a "
                     "secret too, so flag any visible text that gives it away.")
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


# ── The stale-secrets audit ────────────────────────────────────────────────
# The other direction: barred passages the party has in fact learned (in play, or from text they can see
# elsewhere) but that were never unbarred. Revealing is the costly mistake here, so findings come
# unticked and need cited evidence; applying one is an ordinary passage reveal, logged for the session.

STALE_MODEL = None  # the strong default: a wrong "they know this" reveals a secret

STALE_INSTRUCTIONS = """The passages below are the parts of this entry still hidden from the players (||barred||), though the \
entry itself is known to them. Some may be stale: the party has in fact learned them, in play or from \
text they can already see, and nobody unbarred them.

Find only passages the party has DEMONSTRABLY learned. Evidence must be concrete and citable: a session \
entry saying they saw, heard or were told it; a logged reveal; or the unbarred text of an entry or a \
link marked "party knows" that states the same fact. A guess the players could make, or something \
they're likely to work out, is not evidence. An empty list is the usual, good answer.

For each: the field, the passage quoted EXACTLY as it appears between the bars, the evidence (name \
the entry), and "clear" when the evidence states it outright, "likely" when it only strongly implies it."""


def stale_subject(conn, entity: dict) -> Subject:
    etype = ENTITY_TYPES[entity["type"]]
    fields = {}
    if db.visible_to_party(entity):
        for f in etype.fields:
            text = entity["fields"].get(f.key, "")
            if not f.dm_only and knowledge.secret_passages(text):
                fields[f.key] = (f.label, text)
    rows = conn.execute("SELECT entity_name, field, text FROM reveals ORDER BY id").fetchall()
    log = [f"- {r['entity_name']}{' · ' + r['field'] if r['field'] else ''}: {r['text'] or '(the whole entry)'}"
           for r in rows]
    return Subject(entity, fields, log)


def stale_prompt(subject: Subject) -> str:
    lines = [STALE_INSTRUCTIONS, "", f"Entry: {context.ref(subject.entity)}"]
    for key, (label, text) in subject.fields.items():
        lines.append(f"[{key}] {label}, hidden passages:")
        lines += [f"  - {knowledge.untease(p).strip()}" for p in knowledge.secret_passages(text)]
    lines += ["", "Reveals logged so far:", *(subject.extra or ["(none)"])]
    return "\n".join(lines)


def stale_schema(subject: Subject) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["findings"],
        "properties": {"findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["field", "passage", "evidence", "certainty"],
            "properties": {
                "field": {"type": "string", "enum": list(subject.fields)},
                "passage": {"type": "string", "description": "The hidden passage, copied exactly, without the bars."},
                "evidence": {"type": "string", "description": "Where the party learned it: name the entry and say how."},
                "certainty": {"type": "string", "enum": ["clear", "likely"]},
            }}}},
    }


def _same(a: str, b: str) -> bool:
    return " ".join(knowledge.untease(a).split()) == " ".join(knowledge.untease(b).split())


def stale_check(data: dict, subject: Subject) -> tuple[list[dict], list[str]]:
    findings, problems, seen = [], [], set()
    for raw in data.get("findings") or []:
        key, passage = raw.get("field"), context.clean(raw.get("passage", "")).strip(knowledge.MARKER).strip()
        if key not in subject.fields:
            problems.append(f"Finding in unknown field {key!r}.")
            continue
        label, text = subject.fields[key]
        match = next((p for p in knowledge.secret_passages(text) if _same(p, passage)), None)
        if match is None:
            problems.append(f"{passage!r} isn't one of the hidden passages of {label}. Copy it exactly.")
            continue
        if (key, match) in seen:
            continue
        seen.add((key, match))
        certainty = raw.get("certainty") if raw.get("certainty") in ("clear", "likely") else "likely"
        findings.append({"field": key, "field_label": label, "quote": knowledge.untease(match).strip(),
                         "passage": match, "severity": certainty,
                         "reason": context.clean(raw.get("evidence", ""))})
    return findings, problems


def stale_apply(conn, entity: dict, finding: dict) -> None:
    """Unbar the passage (found afresh: earlier reveals shift the positions) and log the reveal."""
    if not db.visible_to_party(entity):
        raise knowledge.PassageGone("The entry itself isn't known to the party any more.")
    text = entity["fields"].get(finding["field"], "")
    passages = knowledge.secret_passages(text)
    index = next((i for i, p in enumerate(passages) if _same(p, finding["passage"])), None)
    if index is None:
        raise knowledge.PassageGone(f"“{finding['quote']}” changed since the check; reveal it by hand.")
    new_text, revealed = knowledge.reveal_passage(text, index, knowledge.passage_tag(passages[index]))
    db.update_entity(conn, entity["id"], entity["name"], dict(entity["fields"], **{finding["field"]: new_text}))
    db.record_reveal(conn, entity, finding["field_label"], revealed)


KINDS: dict[str, Kind] = {
    "secrecy": Kind("secrecy", "Secrecy check", SECRECY_MODEL, secrecy_subject, secrecy_prompt, secrecy_schema,
                    secrecy_check, secrecy_apply),
    "stale": Kind("stale", "Stale secrets", STALE_MODEL, stale_subject, stale_prompt, stale_schema,
                  stale_check, stale_apply),
}
