"""Battle Points: Daggerheart's encounter budget, per the SRD's "Building Balanced Encounters".

Budget = (3 × PCs) + 2, adjusted; adversaries cost points by role. Environments cost nothing. Pure
arithmetic, no database: the composite workspace feeds it ticked cast members and the GM's toggles.
"""
from dataclasses import dataclass, field

COSTS = {"Minion": 1, "Social": 1, "Support": 1, "Horde": 2, "Ranged": 2, "Skulk": 2, "Standard": 2,
         "Leader": 3, "Bruiser": 4, "Solo": 5}
HEAVY = ("Bruiser", "Horde", "Leader", "Solo")  # without any of these the budget gets +1

# The adjustments only the GM can judge. Two-or-more Solos and no-heavy-hitters are applied automatically.
TOGGLES = {
    "easier": ("Easier or shorter fight", -1),
    "harder": ("Harder or longer fight", +2),
    "lower_tier": ("Using an adversary from a lower tier", +1),
    "damage_boost": ("+1d4 (or a static +2) to all adversary damage", -2),
}


@dataclass
class Spend:
    name: str
    role: str
    count: int  # for Minions: groups the size of the party
    cost: int

    @property
    def label(self) -> str:
        unit = "group" if self.role == "Minion" else ""
        many = f"{self.count}× " if self.count > 1 or unit else ""
        what = f"{self.role} {unit}".strip() + ("s" if self.count > 1 and unit else "")
        return f"{many}{what}: {self.name}"


@dataclass
class Meter:
    pcs: int
    base: int
    adjustments: list[tuple[str, int]] = field(default_factory=list)
    spends: list[Spend] = field(default_factory=list)

    @property
    def budget(self) -> int:
        return self.base + sum(delta for _, delta in self.adjustments)

    @property
    def spent(self) -> int:
        return sum(s.cost for s in self.spends)

    @property
    def remaining(self) -> int:
        return self.budget - self.spent

    @property
    def has_adversaries(self) -> bool:
        return bool(self.spends)

    def summary(self) -> str:
        """One line for the encounter sheet's Battle Points field ('' when there's nothing to fight)."""
        if not self.spends:
            return ""
        adjust = "".join(f", {d:+d} {label.lower()}" for label, d in self.adjustments)
        spends = ", ".join(f"{s.label} ({s.cost})" for s in self.spends)
        return (f"Budget {self.budget} (3×{self.pcs}+2{adjust}) · spent {self.spent}: {spends}"
                + (f" · {self.remaining} over budget" if self.remaining < 0 else ""))


def meter(pcs: int, adversaries: list[dict], toggles: set[str] | list[str] = ()) -> Meter:
    """adversaries: [{name, role, count}]. Unknown roles cost nothing (they can't be priced)."""
    spends = []
    for adv in adversaries:
        role, count = adv.get("role", ""), max(1, int(adv.get("count") or 1))
        if role in COSTS:
            spends.append(Spend(adv.get("name", "?"), role, count, COSTS[role] * count))
    adjustments = [TOGGLES[t] for t in TOGGLES if t in set(toggles)]
    if spends:
        if sum(s.count for s in spends if s.role == "Solo") >= 2:
            adjustments.append(("Two or more Solos", -2))
        if not any(s.role in HEAVY for s in spends):
            adjustments.append(("No Bruisers, Hordes, Leaders or Solos", +1))
    return Meter(pcs=pcs, base=3 * pcs + 2, adjustments=adjustments, spends=spends)
