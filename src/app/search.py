"""World-bible search.

Scans entities in Python rather than through a SQLite FTS index on purpose: party-view
search must only see what the party can see (canon, known entities; no DM-only fields;
no ||secrets||). An index over raw fields would let a party search confirm that a hidden
fact exists just by returning a hit. At campaign scale a full scan is effectively free.

Matching is case- and accent-insensitive ("fane" finds "Fané"). Every term must appear
somewhere in the entity (name or a searchable field); "quoted phrases" match as a unit.
"""
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field

from markupsafe import Markup, escape

from . import db, knowledge
from .entity_types import ENTITY_TYPES

SNIPPET_BEFORE = 60
SNIPPET_AFTER = 140
MAX_SNIPPETS = 2


# ── Folding: case/accent-insensitive text with a map back to the original ──

def _fold_char(char: str) -> str:
    decomposed = unicodedata.normalize("NFKD", char.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def fold(text: str) -> str:
    return "".join(_fold_char(c) for c in text)


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for each folded char, the index of the original char it came from."""
    folded, index_map = [], []
    for i, char in enumerate(text):
        for f in _fold_char(char):
            folded.append(f)
            index_map.append(i)
    return "".join(folded), index_map


def parse_query(query: str) -> list[str]:
    """Folded search terms: "quoted phrases" stay whole, everything else splits on whitespace."""
    terms = []
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', query):
        term = " ".join(fold(phrase or word).split())
        if term and term not in terms:
            terms.append(term)
    return terms


# ── Text with per-character secret flags ───────────────────────────────────

@dataclass
class Text:
    plain: str  # markers stripped
    secret: list[bool]  # per char of plain

    @classmethod
    def for_view(cls, raw: str, party_view: bool) -> "Text":
        if party_view:
            plain = knowledge.party_text(raw)
            return cls(plain, [False] * len(plain))
        plain, secret = [], []
        for chunk, is_secret in knowledge.segments(raw):
            plain.append(chunk)
            secret.extend([is_secret] * len(chunk))
        return cls("".join(plain), secret)


def _hit_ranges(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Merged (start, end) ranges in the original text where any term matches."""
    folded, index_map = _fold_with_map(text)
    ranges = []
    for term in terms:
        start = folded.find(term)
        while start != -1:
            end = start + len(term)
            ranges.append((index_map[start], index_map[end - 1] + 1))
            start = folded.find(term, end)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _highlight(text: Text, hits: list[tuple[int, int]], lo: int, hi: int) -> Markup:
    """Render plain[lo:hi] with <mark> on hits and the secret styling on secret chars."""
    hit = [False] * len(text.plain)
    for start, end in hits:
        for i in range(start, end):
            hit[i] = True
    out = []
    i = lo
    while i < hi:
        j = i
        while j < hi and hit[j] == hit[i] and text.secret[j] == text.secret[i]:
            j += 1
        piece = escape(text.plain[i:j])
        if hit[i]:
            piece = Markup("<mark>{}</mark>").format(piece)
        if text.secret[i]:
            piece = Markup('<span class="secret" title="Hidden from the party">{}</span>').format(piece)
        out.append(piece)
        i = j
    return Markup("").join(out)


def snippet(text: Text, hits: list[tuple[int, int]]) -> Markup:
    first = hits[0][0]
    lo = max(0, first - SNIPPET_BEFORE)
    hi = min(len(text.plain), first + SNIPPET_AFTER)
    # Snap to word boundaries so the snippet doesn't start or end mid-word.
    if lo > 0:
        space = text.plain.find(" ", lo, first)
        lo = space + 1 if space != -1 else lo
    if hi < len(text.plain):
        space = text.plain.rfind(" ", hits[0][1], hi)
        hi = space if space != -1 else hi
    body = _highlight(text, hits, lo, hi)
    return Markup("{}{}{}").format("… " if lo > 0 else "", body, " …" if hi < len(text.plain) else "")


# ── Search ─────────────────────────────────────────────────────────────────

@dataclass
class Result:
    entity: dict
    name_html: Markup
    snippets: list[tuple[str, Markup]] = field(default_factory=list)
    score: int = 0


def searchable_fields(entity: dict, party_view: bool) -> list[tuple[str, str]]:
    """(label, raw text) pairs in display order, matching what the detail page shows."""
    etype = ENTITY_TYPES[entity["type"]]
    values = entity["fields"]
    pairs = [(f.label, values.get(f.key, "")) for f in etype.fields
             if not (party_view and f.dm_only)]
    if not party_view:
        spec_keys = {f.key for f in etype.fields}
        pairs += [(key, value) for key, value in values.items() if key not in spec_keys]
    return [(label, str(value)) for label, value in pairs if value]


def match_entity(entity: dict, terms: list[str], party_view: bool) -> Result | None:
    name = Text(entity["name"], [False] * len(entity["name"]))
    name_hits = _hit_ranges(name.plain, terms)
    folded_name = fold(name.plain)
    found = {t for t in terms if t in folded_name}

    matched_fields = []
    for label, raw in searchable_fields(entity, party_view):
        text = Text.for_view(raw, party_view)
        folded = fold(text.plain)
        present = {t for t in terms if t in folded}
        if present:
            found |= present
            matched_fields.append((label, text, len(present)))

    if found != set(terms):
        return None

    score = 100 * len({t for t in terms if t in folded_name})
    if folded_name == " ".join(terms):
        score += 1000
    score += sum(n for _, _, n in matched_fields)

    snippets = [(label, snippet(text, _hit_ranges(text.plain, terms)))
                for label, text, _ in matched_fields[:MAX_SNIPPETS]]
    name_html = _highlight(name, name_hits, 0, len(name.plain))
    return Result(entity, name_html, snippets, score)


def search(conn: sqlite3.Connection, query: str, statuses: tuple[str, ...],
           party_view: bool = False, type_key: str | None = None) -> list[Result]:
    terms = parse_query(query)
    if not terms:
        return []
    if party_view:
        statuses = ("canon",)
    sql = f"SELECT * FROM entities WHERE status IN ({','.join('?' * len(statuses))})"
    params: list = list(statuses)
    if party_view:
        sql += " AND known_to_party = 1"
    if type_key:
        sql += " AND type = ?"
        params.append(type_key)
    results = []
    for row in conn.execute(sql, params):
        entity = db.entity_from_row(row)
        if entity["type"] not in ENTITY_TYPES:
            continue
        result = match_entity(entity, terms, party_view)
        if result:
            results.append(result)
    results.sort(key=lambda r: (-r.score, db.natural_key(r.entity["name"])))
    return results
