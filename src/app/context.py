"""What feeds a generation: the prompt, the output schema, and the checks on what comes back.

This module only decides *what* the model sees (rules, frame, bible) and what a valid answer looks
like. How the answer is obtained lives in llm.py; how a draft is stored and reviewed lives elsewhere.
Everything here is generic: campaign content arrives from the database at call time, never from
this file (the repository is public).
"""
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import db, knowledge
from .entity_types import ENTITY_TYPES, EntityType

CONTEXT_DIR = Path(__file__).resolve().parents[1] / "context"
MODULES = (("Rules: core", "core.md"), ("Rules: adversaries", "adversaries.md"), ("Campaign frame", "witherwild.md"))
TYPE_ORDER = tuple(ENTITY_TYPES)

SYSTEM_PREAMBLE = """\
You are the co-DM for a Daggerheart campaign, drafting new world-bible entries for the game master during session prep. The GM reviews every draft before it becomes canon, so your job is good proposals, clearly flagged wherever they touch what's established.

## What outranks what
1. The world bible below is this table's campaign and the final word. Never contradict a canon entry. Where the bible and the published frame disagree, the bible wins; don't nudge it back toward the book.
2. The campaign frame module is the published setting. Frame material the bible doesn't mention yet is fair game, but it isn't established fact: say so in dm_notes when you lean on it.
3. The core rules and adversary modules govern mechanics. Stat blocks follow them.

## Reading the bible
Each entry shows its id, type, name and status:
- CANON: established. Build on it.
- UNDECIDED: a draft the GM has flagged as open. You may propose answers to its open questions where they fit naturally into what you're creating; that's welcome. Every time you do, list it in open_threads_touched so the GM decides. Never treat an undecided item as settled.
Text in ||double bars|| is a DM-only secret. Entries and links marked "party knows" are known to the players; everything else is hidden from them.

## Secrets in what you write
Write every field as if it might one day be shown to the players, and wrap whatever they shouldn't learn in ||double bars||: true motives, hidden allegiances, what's really going on. Bars always close. Never put a secret in the name. Bible secrets may shape your draft, but stay inside bars in it.

## Craft
- Ground everything. The best draft makes the existing world more connected, not just bigger: tie it to existing people, places, factions and threads.
- Invent freely at small scale (a cousin, a tavern, a grudge). Never redefine gods, history or the nature of established things. If your draft implies something new about an existing canon entry, say so in dm_notes.
- Be specific and playable. Concrete details the GM can say aloud beat mood. Every entry needs something the party can push on: a want, a lever, a problem.
- Match the length and density of comparable entries in the bible. A prep sheet, not a novel.
- Match the tone the bible establishes, not generic fantasy.
- Use the bible's names and spellings exactly."""

# Per-field guidance, carried in the output schema's descriptions. Fields without a hint get none.
FIELD_HINTS: dict[tuple[str, str], str] = {
    ("npc", "secret_or_want"): "What they want, and what they hide. The hidden part usually belongs in ||bars||.",
    ("npc", "hook"): "How the party gets pulled in, or what this person can do for or against them.",
    ("location", "danger_or_mystery"): "What makes it worth visiting or dangerous. Secrets in ||bars||.",
    ("faction", "goals"): "Public aims, and hidden ones in ||bars||.",
    ("arc", "current_state"): "One line: where this stands right now.",
    ("adversary", "description"): "What the party sees: look, sound, behaviour. Player-safe unless barred.",
    ("adversary", "motives_tactics"): "Short comma-separated verbs and aims, per the adversary module.",
    ("adversary", "thresholds"): "Major/severe, like 7/14, per the tier benchmarks.",
    ("adversary", "standard_attack"): "Name: range | damage, like Claws: Melee | 1d8+2 phy.",
    ("adversary", "features"): "One feature per line: Name - Action/Reaction/Passive: effect.",
}

# Fields the model doesn't write directly: the entry's Notes hold the model's DM notes, fully barred.
MODEL_SKIPS = {"notes"}

ADVERSARY_INSTRUCTION = ("Build the full stat block per the adversary module. If the brief doesn't set a tier, "
                         "use the party's tier from the bible, defaulting to tier 1.")


# ── Gathering the bible ────────────────────────────────────────────────────

def context_entities(conn: sqlite3.Connection) -> list[dict]:
    """All canon, plus the drafts the GM opted in. Archived entries never feed generation."""
    rows = conn.execute(
        "SELECT * FROM entities WHERE status = 'canon' OR (status = 'draft' AND ai_context = 1)"
    ).fetchall()
    entities = [db.entity_from_row(r) for r in rows if r["type"] in ENTITY_TYPES]
    # Deterministic order keeps the prompt byte-stable between calls, so it caches.
    return sorted(entities, key=lambda e: (TYPE_ORDER.index(e["type"]), db.natural_key(e["name"]), e["id"]))


def ref(entity: dict) -> str:
    return f"[{entity['type']} #{entity['id']}] {entity['name']}"


def render_entry(entity: dict, links: dict[str, list[dict]], included: set[int]) -> str:
    etype = ENTITY_TYPES[entity["type"]]
    status = "CANON" if entity["status"] == "canon" else "UNDECIDED"
    knows = " · party knows" if db.visible_to_party(entity) else ""
    lines = [f"### {ref(entity)} · {status}{knows}"]
    for f in etype.fields:
        value = entity["fields"].get(f.key, "").strip()
        if value:
            lines.append(f"{f.label}: {value}")
    link_lines = []
    for arrow, key in (("→", "outgoing"), ("←", "incoming")):
        for link in links[key]:
            if link["other_id"] not in included:
                continue
            known = " (party knows)" if link["known_to_party"] else ""
            other = f"[{link['other_type']} #{link['other_id']}] {link['other_name']}"
            link_lines.append(f"{arrow} {link['relation_label']} {other}{known}")
    if link_lines:
        lines.append("Links:")
        lines.extend(f"  {line}" for line in link_lines)
    return "\n".join(lines)


def render_bible(conn: sqlite3.Connection, entities: list[dict]) -> str:
    included = {e["id"] for e in entities}
    return "\n\n".join(render_entry(e, db.links_for(conn, e["id"]), included) for e in entities)


def system_prompt(conn: sqlite3.Connection, entities: list[dict] | None = None) -> str:
    entities = context_entities(conn) if entities is None else entities
    parts = [SYSTEM_PREAMBLE]
    for title, filename in MODULES:
        parts.append(f"## {title}\n\n{(CONTEXT_DIR / filename).read_text(encoding='utf-8').strip()}")
    parts.append("## The world bible\n\n" + (render_bible(conn, entities) or "(empty so far)"))
    return "\n\n".join(parts) + "\n"


# ── The request ────────────────────────────────────────────────────────────

def user_prompt(entity_type: str, brief: str, anchors: list[dict],
                current: dict | None = None, notes: str = "") -> str:
    etype = ENTITY_TYPES[entity_type]
    lines = [f"Draft a new {etype.label}.", f"Brief: {brief.strip() or '(none: use your judgment)'}"]
    if anchors:
        lines.append("Anchors (it must link to each of these):")
        lines.extend(f"- {ref(a)}" for a in anchors)
    if entity_type == "adversary":
        lines.append(ADVERSARY_INSTRUCTION)
    if current is not None:
        lines += [
            "",
            "This replaces the current draft below. "
            f"The GM's notes for this attempt: {notes.strip() or '(none: try a different take)'}",
            "Current draft:",
            json.dumps({"name": current["name"], "fields": current["fields"]}, ensure_ascii=False, indent=2),
            "Change only what the notes ask for, plus anything they force for consistency. "
            "Return only the fields you changed; fields you leave out are kept exactly as they are. "
            "Leave out the name too unless you change it. Always return concept, proposed_links, "
            "open_threads_touched and dm_notes in full for this version.",
        ]
    return "\n".join(lines)


def model_fields(etype: EntityType):
    return [f for f in etype.fields if f.key not in MODEL_SKIPS]


def output_schema(entity_type: str, partial: bool = False) -> dict:
    """JSON schema for one draft. partial=True (regeneration) makes the name and fields optional."""
    etype = ENTITY_TYPES[entity_type]
    props = {}
    for f in model_fields(etype):
        prop: dict = {"type": "string"}
        if f.widget == "select":
            prop["enum"] = list(f.options)
        if f.widget == "number":
            prop["description"] = "A whole number, written as digits."
        hint = FIELD_HINTS.get((entity_type, f.key))
        if hint:
            prop["description"] = (prop.get("description", "") + " " + hint).strip()
        props[f.key] = prop
    fields_schema = {
        "type": "object", "properties": props, "additionalProperties": False,
        "required": [] if partial else list(props),
    }
    required = ["concept", "fields", "proposed_links", "open_threads_touched", "dm_notes"]
    if not partial:
        required.insert(1, "name")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "concept": {"type": "string", "description": "Your pitch in one or two sentences, before any detail."},
            "name": {"type": "string", "description": "No secrets in the name."},
            "fields": fields_schema,
            "proposed_links": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["target_id", "direction", "label", "party_knows", "why"],
                    "properties": {
                        "target_id": {"type": "integer", "description": "Id of an existing bible entry."},
                        "direction": {"type": "string", "enum": ["from_new", "to_new"],
                                      "description": "from_new: <new entry> <label> <target>. to_new: the reverse."},
                        "label": {"type": "string", "description": "Short relation, like 'member of' or 'owes money to'."},
                        "party_knows": {"type": "boolean"},
                        "why": {"type": "string"},
                    },
                },
            },
            "open_threads_touched": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["entry_id", "thread", "proposal"],
                    "properties": {
                        "entry_id": {"type": "integer", "description": "Id of the UNDECIDED entry holding the thread."},
                        "thread": {"type": "string", "description": "The open question, as the bible words it."},
                        "proposal": {"type": "string", "description": "What this draft proposes for it."},
                    },
                },
            },
            "dm_notes": {"type": "string",
                         "description": "Your reasoning and how to use this at the table; frame material and "
                                        "implications for existing entries. The GM only."},
        },
    }


# ── Checking the answer ────────────────────────────────────────────────────

@dataclass
class Draft:
    """A checked answer, ready to store. `fields` is the full field set (merged, on regeneration)."""
    concept: str
    name: str
    fields: dict
    proposed_links: list[dict]
    open_threads: list[dict]
    dm_notes: str
    problems: list[str] = field(default_factory=list)  # empty means valid

    def as_output(self) -> dict:
        return {"concept": self.concept, "name": self.name, "fields": self.fields,
                "proposed_links": self.proposed_links, "open_threads_touched": self.open_threads,
                "dm_notes": self.dm_notes}


def barred(text: str) -> str:
    """Wrap a whole text as a DM-only secret, so it stays hidden even if the entry is revealed."""
    text = text.replace(knowledge.MARKER, "").strip()
    return f"{knowledge.MARKER}{text}{knowledge.MARKER}" if text else ""


def clean(value) -> str:
    """Trim, and undo stray JSON escaping the model sometimes leaves inside its text (\\" for ")."""
    return str(value if value is not None else "").replace('\\"', '"').strip()


def _cleaned(data):
    if isinstance(data, dict):
        return {k: _cleaned(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_cleaned(v) for v in data]
    return clean(data) if isinstance(data, str) else data


def check(data: dict, entity_type: str, entities: list[dict], anchors: list[dict],
          current: dict | None = None) -> Draft:
    """Normalise a model answer and list what's wrong with it. On regeneration, unchanged fields come
    from `current` verbatim, so hand edits survive."""
    etype = ENTITY_TYPES[entity_type]
    data = _cleaned(data)
    by_id = {e["id"]: e for e in entities}
    problems = []

    allowed = {f.key: f for f in model_fields(etype)}
    returned = {k: str(v).strip() for k, v in (data.get("fields") or {}).items() if k in allowed}
    fields = dict(current["fields"]) if current else {}
    fields.update(returned)
    fields = {k: v for k, v in fields.items() if v}
    for key, value in returned.items():
        f = allowed[key]
        if f.widget == "select" and value and value not in f.options:
            problems.append(f"{f.label} must be one of {', '.join(f.options)}, not {value!r}.")
        if f.widget == "number" and value and not value.isdigit():
            problems.append(f"{f.label} must be a whole number, not {value!r}.")
        if knowledge.has_unclosed_secret(value):
            problems.append(f"{f.label} has an unclosed || secret marker.")

    name = str(data.get("name") or (current or {}).get("name") or "").strip()
    if not name:
        problems.append("The draft has no name.")
    if knowledge.MARKER in name:
        problems.append("The name contains a || secret marker.")

    links, seen = [], set()
    for link in data.get("proposed_links") or []:
        target = by_id.get(link.get("target_id"))
        label = " ".join(str(link.get("label", "")).split())
        if target is None:
            problems.append(f"A proposed link points at entry #{link.get('target_id')}, which isn't in the bible.")
            continue
        if not label:
            problems.append(f"The proposed link to {ref(target)} has no label.")
            continue
        key = (target["id"], link.get("direction"), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        links.append({"target_id": target["id"], "target": ref(target),
                      "direction": "to_new" if link.get("direction") == "to_new" else "from_new",
                      "label": label, "party_knows": bool(link.get("party_knows")),
                      "why": str(link.get("why", "")).strip()})
    linked = {l["target_id"] for l in links}
    for anchor in anchors:
        if anchor["id"] not in linked:
            problems.append(f"The draft must link to anchor {ref(anchor)}.")

    threads = []
    for thread in data.get("open_threads_touched") or []:
        holder = by_id.get(thread.get("entry_id"))
        if holder is None or holder["status"] != "draft":
            problems.append(f"open_threads_touched names entry #{thread.get('entry_id')}, "
                            "which isn't an UNDECIDED entry.")
            continue
        threads.append({"entry_id": holder["id"], "entry": ref(holder),
                        "thread": str(thread.get("thread", "")).strip(),
                        "proposal": str(thread.get("proposal", "")).strip()})

    dm_notes = str(data.get("dm_notes", "")).strip()
    if "notes" in {f.key for f in etype.fields}:
        fields["notes"] = barred(dm_notes)  # fully barred: revealing the entry must never reveal these
        if not fields["notes"]:
            del fields["notes"]

    return Draft(concept=str(data.get("concept", "")).strip(), name=name, fields=fields,
                 proposed_links=links, open_threads=threads, dm_notes=dm_notes, problems=problems)


def retry_prompt(prompt: str, problems: list[str]) -> str:
    """The same request again, with what was wrong last time."""
    return (prompt + "\n\nYour previous answer had these problems. Fix them and answer again:\n"
            + "\n".join(f"- {p}" for p in problems))
