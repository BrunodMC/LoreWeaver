"""Inline secrets: text wrapped in ||...|| is known to the DM but hidden from the party.

A secret that starts with a tilde, ||~like this||, is a tease: still hidden, but the party sees that
something is there, as decoy writing struck out in ink (see ornament.py). The words never leave.
"""
import hashlib
import re

from markupsafe import Markup, escape

from . import ornament

MARKER = "||"
TEASE = "~"


def is_tease(passage: str) -> bool:
    return passage.startswith(TEASE)


def untease(passage: str) -> str:
    return passage[len(TEASE):].lstrip() if is_tease(passage) else passage


def has_unclosed_secret(text: str) -> bool:
    return text.count(MARKER) % 2 == 1


def segments(text: str) -> list[tuple[str, bool]]:
    """(chunk, is_secret) pairs. An unclosed marker hides everything after it (fails closed)."""
    return [(part, i % 2 == 1) for i, part in enumerate(text.split(MARKER)) if part]


def _tidy(public: str) -> str:
    """Close the gaps removed secrets leave: doubled spaces, trailing spaces, blank edges."""
    public = re.sub(r"[ \t]{2,}", " ", public)
    return "\n".join(line.rstrip() for line in public.strip().splitlines())


def party_text(text: str) -> str:
    """What the party may read, as plain text (search, audits). Teases count as secrets here."""
    return _tidy("".join(part for part, secret in segments(text) if not secret))


def passage_tag(passage: str) -> str:
    """A short fingerprint of a secret passage, so a reveal can't hit a passage that changed meanwhile."""
    return hashlib.sha1(passage.encode("utf-8")).hexdigest()[:12]


def secret_passages(text: str) -> list[str]:
    """The closed ||...|| passages, in order. A trailing unclosed one isn't addressable."""
    parts = text.split(MARKER)
    closed = len(parts) if len(parts) % 2 == 1 else len(parts) - 1
    return [parts[i] for i in range(1, closed, 2)]


class PassageGone(ValueError):
    """The passage was edited or removed since the page was rendered."""


def reveal_passage(text: str, index: int, tag: str) -> tuple[str, str]:
    """Remove the bars around the index-th secret passage. Returns (new text, the revealed passage)."""
    passages = secret_passages(text)
    if not 0 <= index < len(passages) or passage_tag(passages[index]) != tag:
        raise PassageGone("That passage changed since the page loaded. Reload and try again.")
    parts = text.split(MARKER)
    k = 2 * index + 1  # the passage's part; the markers to drop sit just before parts k and k + 1
    parts[k] = untease(parts[k])  # a revealed tease is plain text, tilde and all gone
    out = parts[0]
    for i in range(1, len(parts)):
        out += ("" if i in (k, k + 1) else MARKER) + parts[i]
    return out, parts[k]


class CannotBar(ValueError):
    """The quote isn't wholly inside the visible text, so it can't be hidden automatically."""


def bar_quote(text: str, quote: str) -> str:
    """Hide the first visible occurrence of quote by wrapping it in ||bars||."""
    quote = quote.strip()
    if not quote or MARKER in quote:
        raise CannotBar("Nothing to hide.")
    parts = text.split(MARKER)
    public_parts = len(parts) if len(parts) % 2 == 1 else len(parts) - 1  # an unclosed tail is secret
    for i in range(0, public_parts, 2):
        at = parts[i].find(quote)
        if at >= 0:
            parts[i] = parts[i][:at] + MARKER + quote + MARKER + parts[i][at + len(quote):]
            return MARKER.join(parts)
    raise CannotBar(f"Couldn't find “{quote}” in the visible text: hide it by hand with Edit.")


def _party_render(text: str, seed: str) -> Markup:
    """Public text, with each closed tease drawn as ink over decoy writing; other secrets vanish."""
    parts = text.split(MARKER)
    closed = len(parts) if len(parts) % 2 == 1 else len(parts) - 1
    marks, out = [], []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(part)
        elif i < closed and is_tease(part):
            out.append(f" \x00{len(marks)}\x00 ")
            marks.append(ornament.tease(len(untease(part)), seed, (i - 1) // 2))
    html = escape(_tidy("".join(out)))
    for n, mark in enumerate(marks):
        html = Markup(html.replace(f"\x00{n}\x00", mark))
    return html


def render(text: str, party_view: bool = False, reveal_url: str = "", seed: str = "") -> Markup:
    """reveal_url (DM view only): give each closed secret a control that reveals just that passage.
    seed (party view): makes each tease's ink unique to its entry and field, and stable."""
    if party_view:
        return _party_render(text, seed)
    pieces, index = [], 0
    addressable = len(secret_passages(text))
    for part, secret in segments(text):
        if not secret:
            pieces.append(escape(part))
            continue
        if index < addressable and is_tease(part):
            span = Markup('<span class="secret tease-dm" title="Teased: the party sees ink struck over it">'
                          '<span class="tease-mark" aria-hidden="true"></span>{}</span>').format(untease(part))
        else:
            span = Markup('<span class="secret" title="Hidden from the party">{}</span>').format(part)
        if reveal_url and index < addressable:
            span += Markup('<button type="button" class="reveal-passage" title="Reveal just this passage to the party" '
                           'hx-post="{}/{}" hx-vals=\'{{"tag": "{}"}}\' hx-confirm="Reveal this passage to the party?">'
                           '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                           'aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/>'
                           '<circle cx="12" cy="12" r="3"/></svg></button>').format(reveal_url, index, passage_tag(part))
        index += 1
        pieces.append(span)
    return Markup("").join(pieces)
