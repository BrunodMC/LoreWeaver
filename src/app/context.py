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
from .entity_types import ADVERSARY_ROLES, ENTITY_TYPES, EntityType

CONTEXT_DIR = Path(__file__).resolve().parents[1] / "context"
MODULES = (("Rules: core", "core.md"), ("Rules: adversaries", "adversaries.md"),
           ("Rules: environments", "environments.md"), ("Campaign frame", "witherwild.md"))
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
- Invent freely at small scale (a cousin, a tavern, a grudge). Never redefine gods, history or the nature of established things. Whenever your draft assumes or implies something new about an existing entry (a name, a past event, a hidden fact), list it in canon_implications: one item per entry and claim, so the GM can accept or reject each.
- Be specific and playable. Concrete details the GM can say aloud beat mood. Every entry needs something the party can push on: a want, a lever, a problem.
- Match the length and density of comparable entries in the bible. A prep sheet, not a novel.
- Match the tone the bible establishes, not generic fantasy.
- Use the bible's names and spellings exactly."""

# Per-field guidance, carried in the output schema's descriptions. Fields without a hint get none.
VOICE_HINT = ("A few short cues for the GM to perform: how they sound, speak or carry themselves. Whatever "
              "makes this character distinct; skip anything that doesn't. Cues, not prose.")

FIELD_HINTS: dict[tuple[str, str], str] = {
    ("npc", "ancestry_role"): "Ancestry and role or occupation, like 'Galapa · Lord-Commander of the Army'. "
                              "A hidden role goes in ||bars||.",
    ("npc", "voice"): VOICE_HINT,
    ("adversary", "voice"): VOICE_HINT,
    ("npc", "secret_or_want"): "What they want, and what they hide. The hidden part usually belongs in ||bars||.",
    ("npc", "hook"): "How the party gets pulled in, or what this person can do for or against them.",
    ("location", "danger_or_mystery"): "What makes it worth visiting or dangerous. Secrets in ||bars||.",
    ("faction", "goals"): "Public aims, and hidden ones in ||bars||.",
    ("arc", "current_state"): "One line: where this stands right now.",
    ("adversary", "individual"): "'One individual' for a single named being (a villain, a unique beast); 'A type' "
                                 "for something there can be many of (a druid, a soldier, a swarm).",
    ("adversary", "description"): "What the party sees: look, sound, behaviour. Player-safe unless barred.",
    ("adversary", "motives_tactics"): "Short comma-separated verbs and aims, per the adversary module.",
    ("adversary", "thresholds"): "Major/severe, like 7/14, per the tier benchmarks.",
    ("adversary", "standard_attack"): "Name: range | damage, like Claws: Melee | 1d8+2 phy.",
    ("adversary", "features"): "One feature per line: Name - Action/Reaction/Passive: effect.",
    ("encounter", "premise"): "What the scene is about: what's happening, who wants what, what makes it turn.",
    ("encounter", "setting"): "Terrain, layout and who is where when the scene opens: concrete enough to sketch.",
    ("encounter", "stakes"): "What is won or lost, for the party and for the others in the scene.",
    ("encounter", "battle_points"): "Budget and spend per the adversary module, like 'Budget 14 · spent 13: "
                                    "2× Standard (4), 1 Leader (3)…'. Empty if the scene has no adversaries.",
    ("encounter", "spoils"): "What the party can come away with, keyed to how the scene ended and to search rolls "
                             "('Search the nest (Instinct 12): with Hope, roll 1d12 on Consumables and…'). Use "
                             "the SRD's Loot and Consumables rolls by rarity and gold in handfuls/bags, per the "
                             "core module; a unique find only if the scene turns on it. Empty if nothing fits.",
    ("encounter", "phases"): "2-4 beats for how the scene escalates or turns, each with its trigger "
                             "(a countdown, an adversary's HP, Fear spent, a choice).",
    ("environment", "description"): "One evocative line: what the party sees and feels. Player-safe unless barred.",
    ("environment", "impulses"): "2-3 short phrases for how the place pushes and pulls people, per the environment module.",
    ("environment", "difficulty"): "A number per the tier benchmarks, or 'Special' if a feature sets it.",
    ("environment", "potential_adversaries"): "Adversaries that might appear here, grouped under fictional labels; "
                                               "prefer ones in the bible.",
    ("environment", "features"): "3-4 features. Each: 'Name - Passive/Action/Reaction: effect' on one line, then "
                                 "its feature questions on the next line. A blank line between features.",
}

# Fields the model never writes: Notes are the GM's own. The model's notes go in its dm_notes answer.
MODEL_SKIPS = {"notes"}

ADVERSARY_INSTRUCTION = ("Build the full stat block per the adversary module. If the brief doesn't set a tier, "
                         "use the party's tier from the bible, defaulting to tier 1.")
ENVIRONMENT_INSTRUCTION = ("Build the full stat block per the environment module. If the brief doesn't set a tier, "
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
    if entity.get("deceased"):
        knows += " · DECEASED" + ("" if entity["deceased"] == "known" else " (the party doesn't know)")
    lines = [f"### {ref(entity)} · {status}{knows}"]
    if entity.get("deceased") and entity.get("death_note"):
        lines.append(f"Death: {entity['death_note']}")
    if entity.get("party_name"):
        lines.append(f"Known to the party only as: {entity['party_name']} (the real name is itself a secret)")
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
                current: dict | None = None, notes: str = "", scene: str = "") -> str:
    etype = ENTITY_TYPES[entity_type]
    lines = [f"Draft a new {etype.label}.", f"Brief: {brief.strip() or '(none: use your judgment)'}"]
    if anchors:
        lines.append("Anchors (it must link to each of these):")
        lines.extend(f"- {ref(a)}" for a in anchors)
    if entity_type == "adversary":
        lines.append(ADVERSARY_INSTRUCTION)
    if entity_type == "environment":
        lines.append(ENVIRONMENT_INSTRUCTION)
    if scene:
        lines.append(scene)
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
            "open_threads_touched, canon_implications and dm_notes in full for this version.",
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
    required = ["concept", "fields", "proposed_links", "open_threads_touched", "canon_implications", "dm_notes"]
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
            "canon_implications": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["entry_id", "implication"],
                    "properties": {
                        "entry_id": {"type": "integer", "description": "Id of the existing entry this is about."},
                        "implication": {"type": "string",
                                        "description": "One new claim about that entry, as a sentence the GM could "
                                                       "paste into its notes."},
                    },
                },
            },
            "dm_notes": {"type": "string",
                         "description": "Your reasoning and how to use this at the table, and any frame material "
                                        "you leaned on. The GM only. Don't repeat canon_implications or "
                                        "open_threads_touched here: those are reviewed separately."},
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
    implications: list[dict] = field(default_factory=list)

    def as_output(self) -> dict:
        return {"concept": self.concept, "name": self.name, "fields": self.fields,
                "proposed_links": self.proposed_links, "open_threads_touched": self.open_threads,
                "canon_implications": self.implications, "dm_notes": self.dm_notes}


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
        links.append({"target_id": target["id"], "target_type": target["type"], "target": ref(target),
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
        threads.append({"entry_id": holder["id"], "entry_type": holder["type"], "entry": ref(holder),
                        "thread": str(thread.get("thread", "")).strip(),
                        "proposal": str(thread.get("proposal", "")).strip()})

    # The model's notes stay out of the entry: they're review material, kept in the generation history
    # and shown in the review panel. Notes (carried over from `current` on regeneration) are the GM's.
    implications = []
    for item in data.get("canon_implications") or []:
        target = by_id.get(item.get("entry_id"))
        if target is None:
            problems.append(f"canon_implications names entry #{item.get('entry_id')}, which isn't in the bible.")
            continue
        # it becomes a barred note on that entry, so nested markers would break the bars
        text = str(item.get("implication", "")).replace(knowledge.MARKER, "").strip()
        if text:
            implications.append({"entry_id": target["id"], "entry_type": target["type"], "entry": ref(target),
                                 "implication": text})

    return Draft(concept=str(data.get("concept", "")).strip(), name=name, fields=fields, implications=implications,
                 proposed_links=links, open_threads=threads,
                 dm_notes=str(data.get("dm_notes", "")).strip(), problems=problems)


def retry_prompt(prompt: str, problems: list[str]) -> str:
    """The same request again, with what was wrong last time."""
    return (prompt + "\n\nYour previous answer had these problems. Fix them and answer again:\n"
            + "\n".join(f"- {p}" for p in problems))


# ── Composite encounters: spines, the cast pool, and the shared scene ──────
# A composite encounter grows in steps the GM checks: three premises ("spines"), then a pool of
# candidate cast members for the chosen one, then the encounter sheet, then each member's draft.

SPINE_COUNT = 3
POOL_SIZE = "8-10"
MORE_SIZE = "4-6"
POOL_TYPES = ("location", "environment", "adversary", "npc", "item", "faction")

ENCOUNTER_SCOPE = ("An encounter is one scene the party could play in a single sitting: a fight, a tense "
                   "negotiation, a dangerous crossing, a discovery, an event. It may mix these.")


def _setup_lines(brief: str, anchors: list[dict], pcs: int) -> list[str]:
    lines = [f"Brief: {brief.strip() or '(none: use your judgment)'}",
             "Respect every constraint the brief sets (where the party is, what's going on, what to avoid)."]
    if anchors:
        lines.append("Anchors (the encounter must involve each of these):")
        lines.extend(f"- {ref(a)}" for a in anchors)
    lines.append(f"Party: {pcs} PCs.")
    return lines


def spine_prompt(brief: str, anchors: list[dict], pcs: int) -> str:
    return "\n".join([
        f"Propose {SPINE_COUNT} distinct premises (\"spines\") for one encounter. {ENCOUNTER_SCOPE}",
        *_setup_lines(brief, anchors, pcs),
        "",
        "Make them genuinely different: a different central conflict or kind of scene each, not three "
        "variations on one idea. Each must be playable as written and grounded in the bible.",
    ])


def spine_schema() -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["spines"],
        "properties": {"spines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "premise", "why"],
            "properties": {
                "title": {"type": "string", "description": "A few words. No secrets."},
                "premise": {"type": "string", "description": "2-4 sentences: what's happening, who wants what, "
                                                             "and what makes the scene turn. Secrets in ||bars||."},
                "why": {"type": "string", "description": "One sentence: how it ties into the bible."},
            }}}},
    }


def check_spines(data: dict) -> tuple[list[dict], list[str]]:
    spines = [{k: clean(s.get(k, "")) for k in ("title", "premise", "why")} for s in (data.get("spines") or [])]
    spines = [s for s in spines if s["title"] and s["premise"]]
    problems = [] if len(spines) >= 2 else [f"Give {SPINE_COUNT} spines, each with a title and a premise."]
    for s in spines:
        if knowledge.has_unclosed_secret(s["premise"]):
            problems.append(f"The premise of {s['title']!r} has an unclosed || secret marker.")
    return spines[:SPINE_COUNT], problems


def pool_prompt(brief: str, anchors: list[dict], pcs: int, spine: str, existing: list[dict] = ()) -> str:
    lines = [f"The GM chose this premise for an encounter. {ENCOUNTER_SCOPE}", f"Premise: {spine.strip()}",
             *_setup_lines(brief, anchors, pcs), ""]
    if existing:
        lines += ["The cast pool so far:", *(f"- {pool_line(m)}" for m in existing), "",
                  f"Propose {MORE_SIZE} more candidates in the same spirit. Don't repeat any of these."]
    else:
        lines.append(f"Propose a pool of {POOL_SIZE} candidate cast members: more than the scene needs, so the "
                     "GM can pick and mix. Offer real alternatives for each slot (two different environments, "
                     "adversaries across several roles, more than one NPC angle).")
    lines += [
        "Mostly locations, environments, adversaries and NPCs. An item or a faction only if the scene turns on it.",
        "Reuse existing bible entries where they fit: set existing_id and use the entry's exact name.",
        "Every candidate must fit the premise and be able to share the scene with the others.",
    ]
    return "\n".join(lines)


def pool_schema() -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["members"],
        "properties": {"members": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["type", "name", "pitch", "role", "count", "existing_id"],
            "properties": {
                "type": {"type": "string", "enum": list(POOL_TYPES)},
                "name": {"type": "string", "description": "No secrets in the name."},
                "pitch": {"type": "string", "description": "One line: who or what, and its part in the scene."},
                "role": {"type": "string", "enum": [*ADVERSARY_ROLES, ""],
                         "description": "Adversaries only: their role per the adversary module. Empty otherwise."},
                "count": {"type": "integer", "description": "Adversaries: how many. For Minions, count GROUPS, "
                                                            "each as many minions as there are PCs: one group is "
                                                            "usually right. Everything else: 1."},
                "existing_id": {"type": "integer", "description": "The id of the bible entry this reuses, or 0 "
                                                                  "for a new one."},
            }}}},
    }


def pool_line(member: dict) -> str:
    """How a cast member reads in prompts: '[adversary · 2× Standard] Name: pitch (the bible's [ref])'."""
    kind = member["type"]
    if kind == "adversary" and member.get("role"):
        kind += f" · {member['count']}× {member['role']}" if member.get("count", 1) > 1 else f" · {member['role']}"
    existing = f" (already in the bible as {member['existing_ref']})" if member.get("existing_id") else ""
    return f"[{kind}] {member['name']}: {member['pitch']}{existing}"


def check_pool(data: dict, entities: list[dict]) -> tuple[list[dict], list[str]]:
    """Normalise proposed cast members. Reused entries take their name, type and role from the bible."""
    by_id = {e["id"]: e for e in entities}
    members, problems = [], []
    for raw in data.get("members") or []:
        raw = _cleaned(raw)
        kind, name, pitch = raw.get("type"), raw.get("name", ""), raw.get("pitch", "")
        role = raw.get("role", "")
        count = raw.get("count") if isinstance(raw.get("count"), int) and raw.get("count") > 0 else 1
        member = {"type": kind, "name": name, "pitch": pitch, "role": "", "count": 1, "existing_id": None,
                  "existing_ref": ""}
        existing_id = raw.get("existing_id") or 0
        if existing_id:
            entry = by_id.get(existing_id)
            if entry is None:
                problems.append(f"A candidate reuses entry #{existing_id}, which isn't in the bible.")
                continue
            member.update(type=entry["type"], name=entry["name"], existing_id=entry["id"], existing_ref=ref(entry))
            kind = entry["type"]
            role = entry["fields"].get("role", "") if kind == "adversary" else ""
        if kind not in POOL_TYPES:
            problems.append(f"{name or 'A candidate'} has type {kind!r}; use one of {', '.join(POOL_TYPES)}.")
            continue
        if not member["name"]:
            problems.append("A candidate has no name.")
            continue
        if knowledge.MARKER in member["name"]:
            problems.append(f"The candidate name {member['name']!r} contains a || secret marker.")
            continue
        if kind == "adversary":
            if role not in ADVERSARY_ROLES and not member["existing_id"]:
                problems.append(f"Adversary {member['name']!r} needs a role: one of {', '.join(ADVERSARY_ROLES)}.")
                continue
            member.update(role=role, count=count)
        members.append(member)
    if not members and not problems:
        problems.append("Propose at least one candidate.")
    return members, problems


SHEET_RULE = """\
This encounter is a composite: you are writing the encounter sheet, the single source of truth that each \
cast member's draft will follow, drafted separately right after you. Fix the facts they will share: the \
layout, where everyone is when the scene opens, numbers, phases and stakes. Refer to cast members by \
their cast names. Leave each member's own details (stat blocks, personalities, backstories) to their drafts."""

MEMBER_RULE = """\
This entry is one member of a composite encounter. The encounter sheet below is the single source of truth \
for the scene: its layout, positions, numbers, phases and stakes. Follow it exactly.
You own only this entry's own details. Don't invent facts about the other cast members or the scene beyond \
what the sheet says; if you need one, keep it vague in your fields and raise it in dm_notes instead.
The other new cast members are being drafted at the same time and aren't in the bible yet: refer to them \
by their cast names, and don't propose links to them (the app links every member to the encounter). Keep \
the name the cast gives you."""


def composite_scene(spine: str, cast: list[dict], sheet: dict | None, budget_line: str) -> str:
    """The block a composite adds to a draft's request: the sheet rule for the encounter itself, or
    the ownership rule plus the current sheet for a member."""
    lines = ["", SHEET_RULE if sheet is None else MEMBER_RULE, "", f"Premise: {spine.strip()}", "Cast:"]
    lines.extend(f"- {pool_line(m)}" for m in cast)
    if budget_line:
        lines.append(f"Battle Points: {budget_line}")
    if sheet is not None:
        etype = ENTITY_TYPES["encounter"]
        lines += ["", f"The encounter sheet: {sheet['name']}"]
        lines.extend(f"{f.label}: {sheet['fields'][f.key]}" for f in etype.fields
                     if f.key != "notes" and sheet["fields"].get(f.key))
    return "\n".join(lines)
