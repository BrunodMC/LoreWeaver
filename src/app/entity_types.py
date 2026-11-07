from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    widget: str = "textarea"  # text | textarea | date


@dataclass(frozen=True)
class EntityType:
    key: str
    label: str
    plural: str
    fields: tuple[Field, ...]


ENTITY_TYPES: dict[str, EntityType] = {
    t.key: t
    for t in (
        EntityType("npc", "NPC", "NPCs", (
            Field("appearance", "Appearance"),
            Field("personality", "Personality"),
            Field("secret_or_want", "Secret or want"),
            Field("hook", "Hook"),
            Field("notes", "Notes"),
        )),
        EntityType("location", "Location", "Locations", (
            Field("atmosphere", "Atmosphere"),
            Field("notable_detail", "Notable detail"),
            Field("danger_or_mystery", "Danger or mystery"),
            Field("notes", "Notes"),
        )),
        EntityType("faction", "Faction", "Factions", (
            Field("goals", "Goals"),
            Field("notes", "Notes"),
        )),
        EntityType("arc", "Arc", "Arcs", (
            Field("summary", "Summary"),
            Field("current_state", "Current state", "text"),
            Field("notes", "Notes"),
        )),
        EntityType("item", "Item", "Items", (
            Field("description", "Description"),
            Field("mechanical_effect", "Mechanical effect"),
            Field("notes", "Notes"),
        )),
        EntityType("session", "Session", "Session log", (
            Field("session_date", "Session date", "date"),
            Field("summary", "Summary"),
            Field("notes", "Notes"),
        )),
    )
}

STATUSES = ("draft", "canon", "archived")
