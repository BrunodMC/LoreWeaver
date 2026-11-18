from src.app import statblock

FEATURES = """Rooted in the Oak - Passive: The brood-mother can't move and can't be knocked back or Restrained.
Pollen Gale - Action: Mark a Stress. Every creature within Close makes an Agility Reaction Roll. On a failure, a target takes 1d8+2 physical damage and is pushed back to Far.
Dust Burst - Reaction: When she takes Major damage or more, every PC within Very Close marks a Stress.
Mosaic of Stolen Light - Fear Feature: Spend a Fear to clear 2 HP. Usable once per scene. ||She was a Faint Divinity once.||
Hatching - Passive: Start a Consequence Countdown (6). Make a Strength Roll (Difficulty 13).
Which cocoons are still too small to hatch?"""


def test_features_get_names_kinds_and_term_emphasis():
    html = statblock.render(FEATURES)
    assert '<span class="feature-name">Pollen Gale</span> <span class="feature-kind">Action</span>' in html
    assert '<span class="feature-kind">Fear Feature</span>' in html
    for term, kind in [("1d8+2 physical damage", "dmg"), ("Agility Reaction Roll", "roll"), ("Very Close", "range"),
                       ("Far", "range"), ("Major damage", "dmg"), ("Restrained", "cond"), ("Usable once per scene", "limit"),
                       ("Consequence Countdown (6)", "count"), ("(Difficulty 13)", "roll"), ("2 HP", "res"), ("Stress", "res")]:
        assert f'<span class="t-{kind}">{term}</span>' in html, term
    assert '<p class="feature-q">Which cocoons are still too small to hatch?</p>' in html
    assert '<span class="secret" title="Hidden from the party">She was a Faint Divinity once.</span>' in html


def test_a_choice_of_traits_is_one_roll():
    assert '<span class="t-roll">Strength or Finesse Roll</span>' in statblock.render("Make a Strength or Finesse Roll.")


def test_unstructured_text_still_renders_safely():
    html = statblock.render("Just a note <b>with tags</b>\n\nand a second line")
    assert "&lt;b&gt;" in html and 'class="feature-gap"' in html and html.count('class="feature-more"') == 2
