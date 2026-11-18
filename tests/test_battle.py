from src.app import battle


def test_budget_and_costs_follow_the_srd():
    m = battle.meter(4, [{"name": "Ford wardens", "role": "Standard", "count": 2},
                         {"name": "Reed captain", "role": "Leader", "count": 1},
                         {"name": "Mudlings", "role": "Minion", "count": 2},
                         {"name": "Toll-keeper", "role": "Social", "count": 1}])
    assert m.base == 14 and m.budget == 14  # a Leader is a heavy hitter: no +1
    assert m.spent == 4 + 3 + 2 + 1
    assert m.remaining == 4
    summary = m.summary()
    assert summary.startswith("Budget 14 (3×4+2) · spent 10:")
    assert "2× Standard: Ford wardens (4)" in summary and "2× Minion groups: Mudlings (2)" in summary


def test_automatic_and_toggled_adjustments():
    light = battle.meter(3, [{"name": "Archer", "role": "Ranged", "count": 1}], {"harder"})
    assert light.budget == 11 + 2 + 1  # harder, and no Bruisers/Hordes/Leaders/Solos
    solos = battle.meter(4, [{"name": "Wyrm", "role": "Solo", "count": 2}], {"easier"})
    assert solos.budget == 14 - 1 - 2
    assert solos.remaining == 11 - 10
    assert "-2 two or more solos" in solos.summary()


def test_no_adversaries_means_no_budget_line():
    m = battle.meter(4, [{"name": "Flooded ford", "role": "", "count": 1}], {"harder"})
    assert not m.has_adversaries and m.summary() == ""
    assert m.adjustments == [battle.TOGGLES["harder"]]  # auto rules only apply to an actual fight


def test_over_budget_is_reported_not_blocked():
    m = battle.meter(1, [{"name": "Brute", "role": "Bruiser", "count": 2}])
    assert m.remaining == 5 - 8
    assert m.summary().endswith("3 over budget")
