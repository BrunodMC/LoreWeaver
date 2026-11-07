from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    widget: str = "textarea"  # text | textarea | date | number | select
    options: tuple[str, ...] = ()  # select only
    dm_only: bool = False  # never shown in party view, whatever the entity's known flag says


@dataclass(frozen=True)
class EntityType:
    key: str
    label: str
    plural: str
    fields: tuple[Field, ...]
    default_known: bool = False
    sort_field: str | None = None  # lists sort by this field first (then by name); None sorts by name only


ADVERSARY_ROLES = ("Bruiser", "Horde", "Leader", "Minion", "Ranged", "Skulk",
                   "Social", "Solo", "Standard", "Support")

ENTITY_TYPES: dict[str, EntityType] = {
    t.key: t
    for t in (
        EntityType("pc", "PC", "PCs", (
            Field("player", "Player", "text"),
            Field("pronouns", "Pronouns", "text"),
            Field("class", "Class", "text"),
            Field("subclass", "Subclass", "text"),
            Field("ancestry", "Ancestry", "text"),
            Field("community", "Community", "text"),
            Field("level", "Level", "number"),
            Field("experiences", "Experiences", "text"),
            Field("appearance", "Appearance"),
            Field("background", "Background"),
            Field("connections", "Connections"),
            Field("notes", "Notes"),
        ), default_known=True),
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
        EntityType("adversary", "Adversary", "Adversaries", (
            Field("description", "Description"),
            Field("tier", "Tier", "select", ("1", "2", "3", "4"), dm_only=True),
            Field("role", "Role", "select", ADVERSARY_ROLES, dm_only=True),
            Field("motives_tactics", "Motives & tactics", "text", dm_only=True),
            Field("difficulty", "Difficulty", "number", dm_only=True),
            Field("thresholds", "Thresholds (major/severe)", "text", dm_only=True),
            Field("hp", "HP", "number", dm_only=True),
            Field("stress", "Stress", "number", dm_only=True),
            Field("attack_modifier", "ATK", "text", dm_only=True),
            Field("standard_attack", "Standard attack (name: range | damage)", "text", dm_only=True),
            Field("experience", "Experience", "text", dm_only=True),
            Field("features", "Features", dm_only=True),
            Field("notes", "Notes"),
        )),
        EntityType("session", "Session", "Session log", (
            Field("session_date", "Session date", "date"),
            Field("summary", "Summary"),
            Field("notes", "Notes"),
        ), sort_field="session_date"),
    )
}

STATUSES = ("draft", "canon", "archived")
