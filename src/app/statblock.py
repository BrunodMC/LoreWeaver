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


RESOURCE_CLASS = {"HP": "hp", "Stress": "stress", "Hope": "hope", "Fear": "fear", "Armor": "armor"}

# Fields that are mechanics, not prose: they get the same emphasis as features. Prose fields don't, since a
# sentence starting "Far from home…" or "Fear gripped…" would light up for nothing.
MECHANICAL = {("adversary", "standard_attack"), ("adversary", "thresholds"), ("adversary", "experience"),
              ("encounter", "battle_points"), ("encounter", "phases"), ("encounter", "spoils")}
ATTACK = re.compile(r"^\s*(?P<name>[^:|\n]{1,60}):\s*(?P<rest>.*)$")


def _term(m: re.Match) -> str:
    kind = m.lastgroup
    if kind == "res":
        word = m.group(0).split()[-1]
        kind = RESOURCE_CLASS.get(word.rstrip("s"), RESOURCE_CLASS.get(word, "res"))
    return f'<span class="t-{kind}">{m.group(0)}</span>'


def _emphasize(text: str) -> Markup:
    return Markup(TERMS.sub(_term, str(escape(text))))


def _inline(text: str) -> Markup:
    """Emphasis on the public parts; a secret passage keeps its own styling and isn't emphasised."""
    pieces = []
    for part, secret in knowledge.segments(text):
        pieces.append(Markup('<span class="secret" title="Hidden from the party">{}</span>').format(part)
                      if secret else _emphasize(part))
    return Markup("").join(pieces)


def mechanics(text: str, field_key: str = "") -> Markup:
    """A mechanical field (attack, thresholds, phases…): line by line, terms emphasised. A standard attack's
    name ('Claws: Melee | 1d8+3 phy') is set off like a feature's."""
    lines = []
    for line in text.splitlines():
        m = ATTACK.match(line) if field_key == "standard_attack" else None
        if m:
            lines.append(Markup('<span class="feature-name">{}</span> {}').format(m["name"].strip(), _inline(m["rest"])))
        else:
            lines.append(_inline(line))
    return Markup("<br>").join(lines)


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
