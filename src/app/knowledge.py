"""Inline secrets: text wrapped in ||...|| is known to the DM but hidden from the party."""
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


def render(text: str, party_view: bool = False) -> Markup:
    if party_view:
        return escape(party_text(text))
    return Markup("").join(
        Markup('<span class="secret" title="Hidden from the party">{}</span>').format(part)
        if secret else escape(part)
        for part, secret in segments(text)
    )
