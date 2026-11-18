"""Stat-block features, set so they can be read at a glance mid-fight.

A feature line reads `Name - Kind: effect` (Kind: Passive, Action, Reaction, Fear Feature…). Its name
stands out and its kind becomes a tag; inside the effect, the game's terms get consistent emphasis
(dice, damage, HP/Stress/Hope/Fear, rolls and Difficulty, ranges, conditions, limits, countdowns). A line
ending in a question is a feature question (environments): set apart, quieter. Secrets stay secrets.
"""
import re

from markupsafe import Markup, escape

from . import knowledge

KINDS = r"(?:Passive|Action|Reaction|Fear Feature|Evolution)"
FEATURE = re.compile(rf"^\s*(?P<name>[^\n:]{{1,70}}?)\s+[-–—]\s+(?P<kind>{KINDS}(?:\s*\([^)]*\))?)\s*:\s*(?P<body>.*)$")

TERMS = re.compile("|".join([
    r"(?P<count>\b(?:Progress |Consequence |Loop(?:ing)? )?Countdown \(\d+\))",
    r"(?P<roll>\b(?:Agility|Strength|Finesse|Instinct|Presence|Knowledge|Spellcast)"
    r"(?:(?:, | or | and )(?:Agility|Strength|Finesse|Instinct|Presence|Knowledge|Spellcast))*(?: Reaction)? Rolls?\b"
    r"|\bReaction Rolls?\b|\bDifficulty(?: of)? \d+\b|\((?:Difficulty )?\d+\))",
    r"(?P<dmg>\b\d*d\d+(?:\s*[+−-]\s*\d+)?(?:\s+(?:physical|magic|direct(?: magic| physical)?|phy|mag))?(?:\s+damage)?\b"
    r"|\b(?:Minor|Major|Severe)(?: damage)?\b|\b(?:physical|magic|direct) damage\b)",
    r"(?P<res>\b(?:\d+\s+)?(?:HP|Stress|Hope|Fear|Armor Slots?)\b)",
    r"(?P<range>\b(?:Very Close|Very Far|Melee|Close|Far)\b(?: range)?)",
    r"(?P<cond>\b(?:Restrained|Vulnerable|Hidden|Stunned|Frightened|Poisoned|Asleep|Dazed)\b)",
    r"(?P<limit>\b(?:[Uu]sable )?once per (?:scene|session|rest|long rest|short rest|GM turn)\b|\bRelentless \(\d+\))",
]))


def _emphasize(text: str) -> Markup:
    escaped = str(escape(text))
    return Markup(TERMS.sub(lambda m: f'<span class="t-{m.lastgroup}">{m.group(0)}</span>', escaped))


def _inline(text: str) -> Markup:
    """Emphasis on the public parts; a secret passage keeps its own styling and isn't emphasised."""
    pieces = []
    for part, secret in knowledge.segments(text):
        pieces.append(Markup('<span class="secret" title="Hidden from the party">{}</span>').format(part)
                      if secret else _emphasize(part))
    return Markup("").join(pieces)


def render(text: str) -> Markup:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(Markup('<div class="feature-gap"></div>'))
            continue
        m = FEATURE.match(line)
        if m:
            out.append(Markup('<p class="feature"><span class="feature-name">{}</span> '
                              '<span class="feature-kind">{}</span> {}</p>')
                       .format(m["name"].strip(), m["kind"].strip(), _inline(m["body"])))
        elif stripped.endswith("?") and knowledge.MARKER not in stripped:
            out.append(Markup('<p class="feature-q">{}</p>').format(stripped))
        else:
            out.append(Markup('<p class="feature-more">{}</p>').format(_inline(stripped)))
    return Markup("").join(out)
