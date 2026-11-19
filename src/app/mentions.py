"""Unlinked mentions: entries that name another entry in their text without a link between them.

No AI: a plain text scan, so it's instant and free, and it can run over the whole bible. It finds
"often works with Kreil Dirn" sitting in Lord Vannis's notes with no link, which matters because links
are what the app walks (a death's ripple, the Briefing's "near the party"). Each finding is the GM's to
link (with a relation of their choosing) or to dismiss; an entry mentioned everywhere (a city, a realm)
can be dismissed as a target altogether.

Names are matched as written, case-sensitively (they're proper nouns), longest first, so "Caer Vannis"
is a mention of the castle and not of Lord Vannis. Besides the full name, an entry answers to its party
alias, its name without a leading "The", the part before a comma ("Nikta, the Great Owl" → "Nikta"),
the parts around brackets ("Stonehaven (Ostara)" → both), and a distinctive word of a person's name
("Kreil" for Kreil Dirn) when no other person shares it.
"""
import re
import sqlite3
from dataclasses import dataclass

from . import db

PEOPLE = ("npc", "pc")  # adversary names are mostly kinds of creature: their words aren't names
TITLES = {"lord", "lady", "sir", "dame", "archmage", "king", "queen", "prince", "princess", "commander",
          "captain", "general", "father", "mother", "brother", "sister", "elder", "master", "mistress", "the"}
MIN_WORD = 4
SNIPPET = 60


def _aliases(entity: dict) -> set[str]:
    names = {entity["name"].strip()}
    if entity.get("party_name"):
        names.add(entity["party_name"].strip())
    out = set()
    for name in names:
        out.add(name)
        bare = re.sub(r"\s*\([^)]*\)", "", name).strip()          # "Stonehaven (Ostara)" → "Stonehaven"
        out.add(bare)
        out.update(p.strip() for p in re.findall(r"\(([^)]*)\)", name) if p.strip() and p.strip() != "unnamed")
        head = bare.split(",")[0].strip()                         # "Nikta, the Great Owl" → "Nikta"
        out.add(head)
        for form in (bare, head):
            if form.lower().startswith("the "):
                out.add(form[4:].strip())                         # "The Spark" → "Spark"
    return {a for a in out if len(a) >= 3}


def _words(entity: dict) -> set[str]:
    """Distinctive words of a person's name: 'Kreil', 'Dirn' (not 'Lord', not 'The')."""
    words = set()
    for name in (entity["name"], entity.get("party_name") or ""):
        bare = re.sub(r"\s*\([^)]*\)", "", name).split(",")[0]
        for word in re.split(r"[\s-]+", bare):
            word = re.sub(r"'s$", "", word.strip(".'\""))
            if len(word) >= MIN_WORD and word[0].isupper() and word.lower() not in TITLES:
                words.add(word)
    return words


def alias_map(entities: list[dict]) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for e in entities:
        for alias in _aliases(e):
            found.setdefault(alias, set()).add(e["id"])
    # single words of people's names, if they point at exactly one person and aren't already a full alias
    by_word: dict[str, set[int]] = {}
    for e in entities:
        if e["type"] in PEOPLE:
            for word in _words(e):
                by_word.setdefault(word, set()).add(e["id"])
    for word, ids in by_word.items():
        if len(ids) == 1 and word not in found:
            found[word] = ids
    return found


def _pattern(aliases) -> re.Pattern | None:
    if not aliases:
        return None
    ordered = sorted(aliases, key=len, reverse=True)  # longest first: "Caer Vannis" before "Vannis"
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(a) for a in ordered) + r")(?![\w])")


@dataclass
class Mention:
    source: dict
    targets: list[dict]  # usually one; several when entries share the name ("The Spark": an arc and an item)
    alias: str
    snippet: str

    @property
    def target(self) -> dict:
        return self.targets[0]


def _snippet(text: str, start: int, end: int) -> str:
    left, right = max(0, start - SNIPPET), min(len(text), end + SNIPPET)
    return ("…" if left else "") + " ".join(text[left:right].split()) + ("…" if right < len(text) else "")


def dismissals(conn: sqlite3.Connection) -> tuple[set[tuple[int, int]], set[int]]:
    rows = conn.execute("SELECT source_id, target_id FROM mention_dismissals").fetchall()
    pairs = {(r[0], r[1]) for r in rows if r[0] is not None}
    targets = {r[1] for r in rows if r[0] is None}
    return pairs, targets


def scan(conn: sqlite3.Connection) -> list[Mention]:
    rows = conn.execute("SELECT * FROM entities WHERE status != 'archived'").fetchall()
    entities = [db.entity_from_row(r) for r in rows]
    by_id = {e["id"]: e for e in entities}
    aliases = alias_map(entities)
    pattern = _pattern(aliases)
    if pattern is None:
        return []
    linked = set()
    for a, b in conn.execute("SELECT from_entity_id, to_entity_id FROM links"):
        linked.add((a, b))
        linked.add((b, a))
    dismissed_pairs, dismissed_targets = dismissals(conn)
    out, seen = [], set()
    for source in sorted(entities, key=lambda e: db.natural_key(e["name"])):
        for text in source["fields"].values():
            if not isinstance(text, str):
                continue
            for match in pattern.finditer(text):
                if text[max(0, match.start() - 5):match.start()] == "From " and text[match.end():match.end() + 1] == ":":
                    continue  # "From <draft>: …", where an accepted claim came from: provenance, not a relation
                group = aliases[match.group(1)]
                if source["id"] in group or any((source["id"], t) in linked for t in group):
                    continue  # names itself, or already linked to (one of) the entries of that name
                targets = [t for t in sorted(group) if (source["id"], t) not in seen
                           and (source["id"], t) not in dismissed_pairs and t not in dismissed_targets]
                if not targets:
                    continue
                seen.update((source["id"], t) for t in targets)
                out.append(Mention(source, [by_id[t] for t in targets], match.group(1),
                                   _snippet(text, match.start(), match.end())))
    return out


def involving(conn: sqlite3.Connection, entity_id: int) -> list[Mention]:
    """Unlinked mentions of this entry by others, and of others by it."""
    return [m for m in scan(conn) if m.source["id"] == entity_id or entity_id in {t["id"] for t in m.targets}]


def dismiss(conn: sqlite3.Connection, source_id: int | None, target_id: int) -> None:
    """Not a link (this pair), or with source_id None: never suggest links to this entry."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO mention_dismissals (source_id, target_id) VALUES (?, ?)",
                     (source_id, target_id))
