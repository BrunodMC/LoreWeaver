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
    party_plural: str | None = None  # what the players' field-book calls the list (None: same as plural)
    sort_field: str | None = None  # lists sort by this field first (then by name); None sorts by name only
    party_hidden: bool = False  # prep material (stat sheets): never shown to the party, even if marked known


ENVIRONMENT_TYPES = ("Exploration", "Social", "Traversal", "Event")

ADVERSARY_ROLES = ("Bruiser", "Horde", "Leader", "Minion", "Ranged", "Skulk",
                   "Social", "Solo", "Standard", "Support")

ENTITY_TYPES: dict[str, EntityType] = {
    t.key: t
    for t in (
        EntityType("pc", "PC", "PCs", party_plural="The Company", fields=(
            Field("player", "Player", "text"),
            Field("gender", "Gender", "text"),  # free text on purpose: "n/a", "neutral", "a choir of moths"…
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
        EntityType("npc", "NPC", "NPCs", party_plural="Folk", fields=(
            Field("ancestry_role", "Ancestry & role", "text"),  # "Galapa · Lord-Commander of the Army"
            Field("gender", "Gender", "text"),
            Field("appearance", "Appearance"),
            Field("voice", "Voice & mannerisms", dm_only=True),  # performance cues for the GM
            Field("personality", "Personality"),
            Field("secret_or_want", "Secret or want"),
            Field("hook", "Hook"),
            Field("notes", "Notes"),
        )),
        EntityType("location", "Location", "Locations", party_plural="Landmarks", fields=(
            Field("atmosphere", "Atmosphere"),
            Field("notable_detail", "Notable detail"),
            Field("danger_or_mystery", "Danger or mystery"),
            Field("notes", "Notes"),
        )),
        EntityType("faction", "Faction", "Factions", party_plural="Powers", fields=(
            Field("goals", "Goals"),
            Field("notes", "Notes"),
        )),
        EntityType("arc", "Arc", "Arcs", party_plural="Threads", fields=(
            Field("summary", "Summary"),
            Field("current_state", "Current state", "text"),
            Field("notes", "Notes"),
        )),
        EntityType("item", "Item", "Items", party_plural="Curios", fields=(
            Field("description", "Description"),
            Field("mechanical_effect", "Mechanical effect"),
            Field("notes", "Notes"),
        )),
        EntityType("adversary", "Adversary", "Adversaries", party_plural="Perils", fields=(
            Field("description", "Description"),
            Field("voice", "Voice & mannerisms", dm_only=True),
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
        EntityType("encounter", "Encounter", "Encounters", party_hidden=True, fields=(
            Field("premise", "Premise"),
            Field("setting", "Setting & layout"),
            Field("stakes", "Stakes"),
            Field("battle_points", "Battle Points", "text", dm_only=True),
            Field("phases", "Phases", dm_only=True),
            Field("spoils", "Spoils", dm_only=True),
            Field("notes", "Notes"),
        )),
        EntityType("environment", "Environment", "Environments", party_hidden=True, fields=(
            Field("description", "Description"),  # one evocative line; the rest is the GM's stat block
            Field("tier", "Tier", "select", ("1", "2", "3", "4"), dm_only=True),
            Field("env_type", "Type", "select", ENVIRONMENT_TYPES, dm_only=True),
            Field("impulses", "Impulses", "text", dm_only=True),
            Field("difficulty", "Difficulty", "text", dm_only=True),  # text: it can be "Special"
            Field("potential_adversaries", "Potential adversaries", "text", dm_only=True),
            Field("features", "Features", dm_only=True),
            Field("notes", "Notes"),
        )),
        EntityType("session", "Session", "Session log", party_plural="Chapters", fields=(
            Field("session_date", "Session date", "date"),
            Field("summary", "Summary"),
            Field("notes", "Notes"),
        ), sort_field="session_date"),
    )
}

STATUSES = ("draft", "canon", "archived")
PARTY_HIDDEN = tuple(k for k, t in ENTITY_TYPES.items() if t.party_hidden)
# SQL for "a type the party may ever see"; the names are our own constants, never user input.
PARTY_TYPES_SQL = "type NOT IN (" + ",".join(f"'{k}'" for k in PARTY_HIDDEN) + ")" if PARTY_HIDDEN else "1 = 1"

# The navigation groups the types (ten in one row overflowed). The party's codex names them in its
# own voice; the DM's archive keeps the plain names.
NAV_GROUPS = (
    ("people", "People", "People", ("npc", "pc", "faction")),  # factions are organised people, not places
    ("places", "Places", "Places", ("location",)),
    ("threats", "Threats", "Perils", ("adversary", "environment", "encounter")),
    ("story", "Story", "Tales", ("arc", "item", "session")),
)
