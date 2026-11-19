from contextlib import closing

import pytest

from src.app import db, generation, llm, proposals


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(generation, "INLINE", True)


@pytest.fixture
def world(conn):
    ids = {
        "mara": db.create_entity(conn, "npc", "Mara Vell", {
            "personality": "Wry, tired.", "secret_or_want": "Wants out. ||Owes the guild a life.||",
            "hook": "Can smuggle anyone.", "notes": "GM scribbles.",
        }, known=True),
        "guild": db.create_entity(conn, "faction", "Ferrymen's Guild", {}),
        "pc": db.create_entity(conn, "pc", "Tamsin", {}, known=True),
    }
    return ids


def script(*responses):
    backend = llm.FakeBackend(list(responses))
    llm.set_backend(backend)
    return backend


def answer(**overrides):
    data = {
        "summary": "Winter broke her nerve.",
        "fields": {"personality": "Wry, and newly afraid.", "hook": "Can smuggle anyone."},  # hook unchanged
        "reasons": [{"field": "personality", "why": "The guild came for her twice."}],
        "proposed_links": [],
        "dm_notes": "Play her jumpier.",
    }
    data.update(overrides)
    return data


def run(conn, world, choice=None, request=None):
    return proposals.run(conn, "time_passes", world["mara"], request or {"elapsed": "a winter"}, choice)


def test_a_proposal_keeps_only_real_changes(conn, world):
    backend = script(answer())
    found = proposals.get(conn, run(conn, world))
    assert found["changes"] == {"personality": "Wry, and newly afraid."}  # the unchanged hook is dropped
    assert found["base"] == {"personality": "Wry, tired."}
    assert found["reasons"] == {"personality": "The guild came for her twice."}
    assert found["status"] == "open"
    assert db.get_entity(conn, world["mara"])["fields"]["personality"] == "Wry, tired."  # nothing applied yet
    call = backend.calls[0]
    assert "a winter" in call["prompt"] and "||Owes the guild a life.||" in call["prompt"]
    assert "GM scribbles" not in call["prompt"]  # Notes are the GM's: not offered for revision
    assert "notes" not in call["schema"]["properties"]["fields"]["properties"]


def test_the_model_is_a_per_call_choice(conn, world):
    backend = script(answer(), answer())
    run(conn, world)
    run(conn, world, "haiku")
    assert backend.calls[0]["model"] == llm.MODEL_CHOICES["opus"][0]  # the kind's default
    assert backend.calls[1]["model"] == llm.MODEL_CHOICES["haiku"][0]
    script(answer())
    run(conn, world, "nonsense")
    assert proposals.latest_open(conn, world["mara"])["request"]["model_choice"] == "opus"


def test_a_new_proposal_replaces_the_open_one(conn, world):
    script(answer(), answer())
    first, second = run(conn, world), run(conn, world)
    assert proposals.get(conn, first)["status"] == "closed"
    assert proposals.latest_open(conn, world["mara"])["id"] == second


def test_an_unclosed_secret_is_retried(conn, world):
    backend = script(answer(fields={"personality": "Afraid. ||Of the guild."}), answer())
    run(conn, world)
    assert len(backend.calls) == 2 and "unclosed" in backend.calls[1]["prompt"]


def test_it_gives_up_after_the_retry(conn, world):
    bad = answer(fields={"personality": "Afraid. ||Of the guild."})
    script(bad, bad)
    with pytest.raises(proposals.ProposalFailed):
        run(conn, world)


def test_links_are_checked_and_deduplicated(conn, world):
    db.add_link(conn, world["mara"], world["pc"], "trusts")
    script(answer(proposed_links=[
        {"target_id": world["guild"], "direction": "to_entry", "label": "hunts", "party_knows": False, "why": "debt"},
        {"target_id": world["guild"], "direction": "to_entry", "label": "hunts", "party_knows": False, "why": "dup"},
        {"target_id": world["pc"], "direction": "from_entry", "label": "trusts", "party_knows": True, "why": "old"},
    ]))
    links = proposals.get(conn, run(conn, world))["links"]
    assert len(links) == 1
    assert (links[0]["from_id"], links[0]["to_id"], links[0]["label"]) == (world["guild"], world["mara"], "hunts")


def test_a_link_to_nowhere_is_retried(conn, world):
    backend = script(answer(proposed_links=[
        {"target_id": 9999, "direction": "from_entry", "label": "x", "party_knows": False, "why": ""}]), answer())
    run(conn, world)
    assert len(backend.calls) == 2


def test_apply_takes_only_what_was_ticked(conn, world):
    script(answer(fields={"personality": "Afraid.", "hook": "Wants passage north."},
                  proposed_links=[{"target_id": world["guild"], "direction": "to_entry", "label": "hunts",
                                   "party_knows": False, "why": ""}]))
    found = proposals.get(conn, run(conn, world))
    assert proposals.apply(conn, found, {"hook"}, {0}) == []
    mara = db.get_entity(conn, world["mara"])
    assert mara["fields"]["hook"] == "Wants passage north." and mara["fields"]["personality"] == "Wry, tired."
    assert mara["fields"]["notes"] == "GM scribbles."
    assert any(l["other_id"] == world["guild"] for l in db.links_for(conn, world["mara"])["incoming"])
    closed = proposals.get(conn, found["id"])
    assert closed["status"] == "closed"
    assert closed["state"] == {"personality": "rejected", "hook": "applied", "link:0": "applied"}


def test_a_hand_edit_made_since_is_never_overwritten(conn, world):
    script(answer())
    found = proposals.get(conn, run(conn, world))
    mara = db.get_entity(conn, world["mara"])
    db.update_entity(conn, mara["id"], mara["name"], {**mara["fields"], "personality": "My own words."})
    assert proposals.apply(conn, found, {"personality"}, set()) == ["personality"]
    assert db.get_entity(conn, world["mara"])["fields"]["personality"] == "My own words."
    assert proposals.get(conn, found["id"])["state"]["personality"] == "conflict"


def test_dismiss_changes_nothing(conn, world):
    script(answer())
    found = proposals.get(conn, run(conn, world))
    proposals.dismiss(conn, found)
    assert proposals.latest_open(conn, world["mara"]) is None
    assert db.get_entity(conn, world["mara"])["fields"]["personality"] == "Wry, tired."


def test_word_diff_marks_cuts_and_additions():
    out = str(proposals.word_diff("Wry, tired.", "Wry, and <afraid>."))
    assert "<del>tired.</del>" in out and "<ins>and &lt;afraid&gt;.</ins>" in out and out.startswith("Wry,")


# ── Through the app ────────────────────────────────────────────────────────

@pytest.fixture
def mara(client):
    with closing(db.get_connection()) as conn:
        return db.create_entity(conn, "npc", "Mara Vell", {"personality": "Wry, tired."}, known=True)


def test_the_form_shows_where_time_passes_fits(client, mara):
    page = client.get(f"/npc/{mara}").text
    assert "Time passes…" in page and 'value="haiku"' in page
    with closing(db.get_connection()) as conn:
        pc = db.create_entity(conn, "pc", "Tamsin", {}, known=True)
    assert "Time passes…" not in client.get(f"/pc/{pc}").text
    client.get("/view/party")
    assert "Time passes…" not in client.get(f"/npc/{mara}").text


def test_propose_then_apply_through_the_page(client, mara):
    backend = script(answer(fields={"personality": "Wry, and newly afraid."}))
    client.post(f"/npc/{mara}/time-passes", data={"elapsed": "a winter", "events": "The river froze.",
                                                  "model": "sonnet"})
    assert backend.calls[0]["model"] == llm.MODEL_CHOICES["sonnet"][0]
    assert "The river froze." in backend.calls[0]["prompt"]
    page = client.get(f"/npc/{mara}").text
    assert "Time passes: proposed changes" in page and "<ins>" in page
    with closing(db.get_connection()) as conn:
        pid = proposals.latest_open(conn, mara)["id"]
    client.post(f"/npc/{mara}/proposals/{pid}/apply", data={"field": "personality"})
    with closing(db.get_connection()) as conn:
        assert db.get_entity(conn, mara)["fields"]["personality"] == "Wry, and newly afraid."
    assert "Time passes: proposed changes" not in client.get(f"/npc/{mara}").text


def test_a_kept_edit_is_reported_on_the_page(client, mara):
    assert "left as you wrote it" in client.get(f"/npc/{mara}?kept=personality").text


def test_elapsed_time_is_required(client, mara):
    assert client.post(f"/npc/{mara}/time-passes", data={"elapsed": "  "}).status_code == 422


def test_a_failed_call_says_so_and_reopens_the_form(client, mara):
    script(llm.LLMError("the loom snapped"))
    client.post(f"/npc/{mara}/time-passes", data={"elapsed": "a year"})
    page = client.get(f"/npc/{mara}").text
    assert "the loom snapped" in page and '<details class="time-passes" open>' in page


def test_the_briefing_lists_open_proposals(client, mara):
    script(answer(summary="She hardened.", fields={"personality": "Hard."}))
    client.post(f"/npc/{mara}/time-passes", data={"elapsed": "a year"})
    page = client.get("/briefing").text
    assert "1 change proposed" in page and "She hardened." in page


def test_players_cannot_ask_or_apply(player, mara):
    assert player.post(f"/npc/{mara}/time-passes", data={"elapsed": "a year"}).status_code == 403
    assert player.post(f"/npc/{mara}/proposals/1/apply", data={}).status_code == 403
