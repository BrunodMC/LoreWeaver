"""Portraits: images the DM uploads for entries, and the prompt to make one elsewhere.

Images live beside the bible, in data/portraits/ (gitignored like the rest of data/), named by entry id.
They are served through the app, which applies the same party-visibility rules as everything else.
The prompt combines one campaign-wide style (so every portrait looks of a piece) with what the party can
see of the entry: a portrait is shown to the players, so it must not paint a secret.
"""
from pathlib import Path

from . import db, knowledge

TYPES = ("npc", "pc", "adversary")  # entries that get a portrait
FORMATS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_BYTES = 8 * 1024 * 1024
STYLE_KEY = "portrait_style"
DEFAULT_STYLE = ("Painted character portrait, head and shoulders, in a consistent illustrated style: textured "
                 "brushwork, muted earthy palette with one accent colour, soft dramatic side light, plain dark "
                 "background. No text, no frame.")


class PortraitError(ValueError):
    pass


def folder() -> Path:
    return db.db_path().parent / "portraits"


def find(entity_id: int) -> Path | None:
    for ext in set(FORMATS.values()):
        path = folder() / f"{entity_id}{ext}"
        if path.exists():
            return path
    return None


def save(entity_id: int, content_type: str, data: bytes) -> Path:
    ext = FORMATS.get(content_type)
    if ext is None:
        raise PortraitError("Upload a PNG, JPEG, WebP or GIF image.")
    if not data:
        raise PortraitError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise PortraitError("That image is over 8 MB; save a smaller copy and try again.")
    folder().mkdir(parents=True, exist_ok=True)
    remove(entity_id)  # one portrait per entry, whatever its format
    path = folder() / f"{entity_id}{ext}"
    path.write_bytes(data)
    return path


def remove(entity_id: int) -> None:
    for ext in set(FORMATS.values()):
        (folder() / f"{entity_id}{ext}").unlink(missing_ok=True)


def style(conn) -> str:
    return db.get_setting(conn, STYLE_KEY) or DEFAULT_STYLE


def prompt(conn, entity: dict) -> str:
    """The style, then the subject as the party could describe it (no secrets, no hidden name)."""
    fields = entity["fields"]
    lines = [style(conn), ""]
    role = knowledge.party_text(fields.get("ancestry_role", ""))
    if role:
        lines.append(f"Subject: {role}.")
    for key in ("appearance", "description"):
        seen = knowledge.party_text(fields.get(key, ""))
        if seen:
            lines.append(f"Appearance: {seen}")
    if fields.get("gender"):
        lines.append(f"Gender: {knowledge.party_text(fields['gender'])}.")
    return "\n".join(line for line in lines).strip()
