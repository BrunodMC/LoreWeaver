import pytest

from src.app import context, db
from src.app.entity_types import ENTITY_TYPES


@pytest.fixture
def bible(conn):
    ids = {
        "mara": db.create_entity(conn, "npc", "Mara Vell", {
            "personality": "Brisk. ||Informs for the guild.||", "hook": "Lost ledger."}, known=True),
        "guild": db.create_entity(conn, "faction", "Ferrymen's Guild", {"goals": "Control the river."}),
        "todo": db.create_entity(conn, "arc", "Open threads", {"notes": "Who sank the barge?"}, status="draft"),
        "shell": db.create_entity(conn, "pc", "Unfinished PC", {}, status="draft"),
        "gone": db.create_entity(conn, "location", "Drowned Mill", {"atmosphere": "Silt."}, status="archived"),
    }
    db.set_ai_context(conn, ids["todo"], True)
    db.add_link(conn, ids["mara"], ids["guild"], "member of", known=True)
    db.add_link(conn, ids["mara"], ids["shell"], "distrusts")  # to an excluded draft: must not show
    return ids


def test_context_is_canon_plus_opted_in_drafts(conn, bible):
    names = [e["name"] for e in context.context_entities(conn)]
    assert names == ["Mara Vell", "Ferrymen's Guild", "Open threads"]  # entity-type order, then name


def test_system_prompt_renders_bible_with_status_secrets_and_links(conn, bible):
    prompt = context.system_prompt(conn)
    assert f"### [npc #{bible['mara']}] Mara Vell · CANON · party knows" in prompt
    assert "Personality: Brisk. ||Informs for the guild.||" in prompt  # DM-facing: secrets stay, barred
    assert f"→ member of [faction #{bible['guild']}] Ferrymen's Guild (party knows)" in prompt
    assert f"← member of [npc #{bible['mara']}] Mara Vell (party knows)" in prompt
    assert f"### [arc #{bible['todo']}] Open threads · UNDECIDED" in prompt
    assert "Unfinished PC" not in prompt and "distrusts" not in prompt and "Drowned Mill" not in prompt
    for title, _ in context.MODULES:
        assert f"## {title}" in prompt
    assert prompt == context.system_prompt(conn)  # byte-stable, so it caches


def test_empty_bible_still_renders(conn):
    assert "## The world bible\n\n(empty so far)" in context.system_prompt(conn)


def test_user_prompt_lists_anchors_and_adversary_rule(conn, bible):
    mara = db.get_entity(conn, bible["mara"])
    prompt = context.user_prompt("adversary", "river bandits", [mara])
    assert "Draft a new Adversary." in prompt and "Brief: river bandits" in prompt
    assert f"- [npc #{bible['mara']}] Mara Vell" in prompt
    assert context.ADVERSARY_INSTRUCTION in prompt
    assert "Current draft" not in prompt
    assert context.ADVERSARY_INSTRUCTION not in context.user_prompt("npc", "", [])


def test_regeneration_prompt_carries_notes_and_current_draft():
    current = {"name": "Old Tam", "fields": {"hook": "Sells maps."}}
    prompt = context.user_prompt("npc", "a mapmaker", [], current=current, notes="darker hook")
    assert "notes for this attempt: darker hook" in prompt
    assert '"hook": "Sells maps."' in prompt
    assert "Return only the fields you changed" in prompt


def test_output_schema_follows_the_entity_type():
    schema = context.output_schema("adversary")
    fields = schema["properties"]["fields"]
    assert "notes" not in fields["properties"]  # the model's DM notes fill Notes instead
    assert fields["properties"]["role"]["enum"] == list(ENTITY_TYPES["adversary"].fields[2].options)
    assert set(fields["required"]) == set(fields["properties"])
    assert "name" in schema["required"]
    assert "Major/severe" in fields["properties"]["thresholds"]["description"]

    partial = context.output_schema("adversary", partial=True)
    assert partial["properties"]["fields"]["required"] == []
    assert "name" not in partial["required"] and "concept" in partial["required"]


def answer(**overrides):
    data = {
        "concept": "A ferry pilot who knows too much.",
        "name": "Ossa Grell",
        "fields": {"gender": "female", "personality": "Wry. ||Paid by the guild.||", "hook": "Can smuggle anyone."},
        "proposed_links": [],
        "open_threads_touched": [],
        "dm_notes": "Pairs well with the barge mystery.",
    }
    data.update(overrides)
    return data


def test_check_accepts_a_good_answer_and_bars_the_dm_notes(conn, bible):
    entities = context.context_entities(conn)
    mara = db.get_entity(conn, bible["mara"])
    data = answer(
        proposed_links=[
            {"target_id": bible["mara"], "direction": "to_new", "label": " owes  money to ", "party_knows": False, "why": "debt"},
            {"target_id": bible["mara"], "direction": "to_new", "label": "Owes money to", "party_knows": True, "why": "dupe"},
        ],
        open_threads_touched=[{"entry_id": bible["todo"], "thread": "Who sank the barge?", "proposal": "Ossa did."}],
    )
    draft = context.check(data, "npc", entities, [mara])
    assert draft.problems == []
    assert draft.fields["notes"] == "||Pairs well with the barge mystery.||"
    assert [l["label"] for l in draft.proposed_links] == ["owes money to"]  # normalised and deduped
    assert draft.proposed_links[0]["target"] == f"[npc #{bible['mara']}] Mara Vell"
    assert draft.open_threads[0]["proposal"] == "Ossa did."


def test_check_reports_every_kind_of_problem(conn, bible):
    entities = context.context_entities(conn)
    guild = db.get_entity(conn, bible["guild"])
    data = answer(
        name="Ossa ||the traitor||",
        fields={"personality": "Wry. ||unclosed", "gender": "", "unknown_field": "x"},
        proposed_links=[
            {"target_id": 999, "direction": "from_new", "label": "knows", "party_knows": False, "why": ""},
            {"target_id": bible["shell"], "direction": "from_new", "label": "knows", "party_knows": False, "why": ""},
            {"target_id": bible["mara"], "direction": "from_new", "label": "  ", "party_knows": False, "why": ""},
        ],
        open_threads_touched=[{"entry_id": bible["mara"], "thread": "x", "proposal": "y"}],
    )
    draft = context.check(data, "npc", entities, [guild])
    text = "\n".join(draft.problems)
    assert "unclosed || secret" in text
    assert "name contains a || secret" in text
    assert "#999" in text and f"#{bible['shell']}" in text  # excluded drafts aren't link targets
    assert "has no label" in text
    assert "must link to anchor [faction" in text
    assert f"entry #{bible['mara']}, which isn't an UNDECIDED entry" in text
    assert "unknown_field" not in draft.fields


def test_check_validates_adversary_menus_and_numbers(conn):
    data = answer(fields={"role": "Tank", "tier": "1", "hp": "six", "difficulty": "12"})
    problems = "\n".join(context.check(data, "adversary", [], []).problems)
    assert "Role must be one of" in problems and "HP must be a whole number" in problems
    assert "Tier" not in problems and "Difficulty" not in problems


def test_regeneration_keeps_unreturned_fields_verbatim(conn):
    current = {"name": "Ossa Grell", "fields": {
        "personality": "Wry, hand-edited by the GM.", "hook": "Can smuggle anyone.", "notes": "||old notes||"}}
    data = {"concept": "Darker take.", "fields": {"hook": "Owes the drowned a debt."},
            "proposed_links": [], "open_threads_touched": [], "dm_notes": "Darker."}
    draft = context.check(data, "npc", [], [], current=current)
    assert draft.problems == []
    assert draft.name == "Ossa Grell"
    assert draft.fields == {"personality": "Wry, hand-edited by the GM.", "hook": "Owes the drowned a debt.",
                            "notes": "||Darker.||"}


def test_check_undoes_stray_json_escaping(conn):
    data = answer(fields={"hook": 'Calls them \\"the quiet ones\\".'}, dm_notes='Say \\"hush\\".')
    draft = context.check(data, "npc", [], [])
    assert draft.fields["hook"] == 'Calls them "the quiet ones".'
    assert draft.dm_notes == 'Say "hush".'


def test_barred_strips_inner_markers_and_handles_empty():
    assert context.barred("a ||b|| c") == "||a b c||"
    assert context.barred("  ") == ""


def test_retry_prompt_lists_problems():
    prompt = context.retry_prompt("Draft a new NPC.", ["The draft has no name."])
    assert prompt.startswith("Draft a new NPC.") and "- The draft has no name." in prompt
