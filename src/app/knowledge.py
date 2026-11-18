"""Inline secrets: text wrapped in ||...|| is known to the DM but hidden from the party."""
import hashlib
import re

from markupsafe import Markup, escape

MARKER = "||"


def has_unclosed_secret(text: str) -> bool:
    return text.count(MARKER) % 2 == 1


def segments(text: str) -> list[tuple[str, bool]]:
    """(chunk, is_secret) pairs. An unclosed marker hides everything after it (fails closed)."""
    return [(part, i % 2 == 1) for i, part in enumerate(text.split(MARKER)) if part]


def party_text(text: str) -> str:
    public = "".join(part for part, secret in segments(text) if not secret)
    public = re.sub(r"[ \t]{2,}", " ", public)
    return "\n".join(line.rstrip() for line in public.strip().splitlines())


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
    out = parts[0]
    for i in range(1, len(parts)):
        out += ("" if i in (k, k + 1) else MARKER) + parts[i]
    return out, passages[index]


def render(text: str, party_view: bool = False, reveal_url: str = "") -> Markup:
    """reveal_url (DM view only): give each closed secret a control that reveals just that passage."""
    if party_view:
        return escape(party_text(text))
    pieces, index = [], 0
    addressable = len(secret_passages(text))
    for part, secret in segments(text):
        if not secret:
            pieces.append(escape(part))
            continue
        span = Markup('<span class="secret" title="Hidden from the party">{}</span>').format(part)
        if reveal_url and index < addressable:
            span += Markup('<button type="button" class="reveal-passage" title="Reveal just this passage to the party" '
                           'hx-post="{}/{}" hx-vals=\'{{"tag": "{}"}}\' hx-confirm="Reveal this passage to the party?">'
                           '👁</button>').format(reveal_url, index, passage_tag(part))
        index += 1
        pieces.append(span)
    return Markup("").join(pieces)
