import re

from src.app import knowledge, ornament


def test_a_tease_is_stable_per_seed_and_varies_between_seeds():
    assert ornament.tease(46, 9, "hook", 0) == ornament.tease(46, 9, "hook", 0)
    assert ornament.tease(46, 9, "hook", 0) != ornament.tease(46, 9, "hook", 1)
    styles = {re.search(r'tease-(\w+)', ornament.tease(30, i, "x", 0)).group(1) for i in range(40)}
    assert styles == set(ornament.TEASE_STYLES)  # both styles turn up across the bible


def test_decoy_matches_the_secret_length_roughly():
    for length in (5, 46, 120):
        decoy = " ".join(re.findall(r'<span class="decoy">([^<]*)</span>', ornament.tease(length, 1, "f", 0)))
        assert length <= len(decoy) <= length + 12


def test_party_view_shows_ink_never_the_words():
    text = "Calm. ||~Burned the ledger at dawn.|| Kind. ||Owes the guild.||"
    party = knowledge.render(text, party_view=True, seed="9:hook")
    assert "tease" in party and "Calm." in party and "Kind." in party
    assert "ledger" not in party and "dawn" not in party and "guild" not in party
    assert knowledge.party_text(text) == "Calm. Kind."  # search and audits see no tease at all
    dm = knowledge.render(text, reveal_url="/npc/9/reveal/hook")
    assert "tease-dm" in dm and "Burned the ledger at dawn." in dm and "~" not in dm
    assert dm.count("reveal-passage") == 2


def test_an_unclosed_tease_stays_hidden_without_ink():
    party = knowledge.render("Calm. ||~half written", party_view=True, seed="1:f")
    assert party == "Calm." and "tease" not in party


def test_revealing_a_tease_drops_the_tilde():
    text = "Calm. ||~ Burned the ledger.|| Kind."
    new, passage = knowledge.reveal_passage(text, 0, knowledge.passage_tag("~ Burned the ledger."))
    assert new == "Calm. Burned the ledger. Kind." and passage == "Burned the ledger."


def test_cracks_and_portraits_are_seeded_and_sound():
    assert ornament.cracks("npc", 9) == ornament.cracks("npc", 9) != ornament.cracks("npc", 10)
    assert ornament.portrait("npc", 9) != ornament.portrait("npc", 10)
    for seed in range(150):  # every seed draws without error or NaN
        for mark in (ornament.cracks("npc", seed), ornament.portrait("npc", seed), ornament.tease(80, seed, "f", 1),
                     ornament.lichen("npc", seed), ornament.foxing("npc", seed)):
            drawing = re.sub(r'<span class="decoy">[^<]*</span>', "", mark)  # decoy words may spell "nan"
            assert "nan" not in drawing.lower()
