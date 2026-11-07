# Daggerheart Adversaries — GM Context Module

Checked against the Daggerheart SRD 2.0 (2026-08-25), p. 93–95 and its 262 adversary stat blocks.

## Core Principle

Build adversaries **fiction first, type second, numbers last**. Start from what the creature does narratively, choose its type, then apply tier-appropriate statistics. Every adversary must answer: **"What makes fighting this enemy different from any other?"** If unclear, add one defining feature tied to the fiction.

## Stat Block Structure (p. 93)

Every adversary has:
- **Name, Tier, Type.** Hordes also note how many creatures each HP represents: `Horde (X/HP)`; a 2/HP horde with 3 HP is a group of six.
- **Description:** appearance and demeanor, one or two sentences.
- **Motives & Tactics:** 3–5 verbs or short phrases (e.g. "Burrow, drag away, feed, reposition").
- **Difficulty:** the target for any roll against them. Non-attack rolls against them can add one relevant Experience.
- **Thresholds:** Major/Severe, e.g. `8/15`. Minions have none.
- **HP and Stress.**
- **ATK:** attack modifier.
- **Standard attack:** `Name: Range | damage type`, e.g. `Claws: Very Close | 1d12+2 phy`. Damage types are `phy` and `mag`. Ranges are Melee, Very Close, Close, Far, Very Far.
- **Experience (optional):** `Name +X`. The GM spends a Fear to add it to an attack roll or to the Difficulty of a roll against the adversary.
- **Features:** written `Name - Kind: effect`, where Kind is Passive, Action or Reaction. Fear Features cost Fear on top of any Fear spent to spotlight the adversary.

Example:
```
BEAR — Tier 1 Bruiser
A large bear with thick fur and powerful claws.
Motives & Tactics: Climb, defend territory, pummel, track
Difficulty: 14 | Thresholds: 9/17 | HP: 7 | Stress: 2
ATK: +1 | Claws: Melee | 1d8+3 phy
Experience: Ambusher +3, Keen Senses +2
Overwhelming Force - Passive: Targets who mark HP from the Bear's standard attack are knocked back to Very Close range.
Bite - Action: Mark a Stress to attack a target within Melee. On a success, deal 3d4+10 physical damage and the target is Restrained until they break free with a successful Strength Roll.
Momentum - Reaction: When the Bear makes a successful attack against a PC, you gain a Fear.
```

## Adversary Types (p. 93)

| Type | Identity |
|------|----------|
| **Bruiser** | Tough; delivers powerful attacks |
| **Horde** | Group of identical creatures acting as one unit |
| **Leader** | Commands and summons other adversaries |
| **Minion** | Easily dispatched but dangerous in numbers |
| **Ranged** | Fragile up close; high damage at range |
| **Skulk** | Maneuvers and exploits openings to ambush |
| **Social** | Challenges through conversation, not combat |
| **Solo** | A formidable challenge for the whole party |
| **Standard** | Representative of their fictional group |
| **Support** | Enhances allies and disrupts opponents |

## Tier Benchmarks (p. 95)

| Tier (levels) | ATK | Damage dice | Difficulty | Major / Severe |
|------|-----|-------------|------------|----------------|
| 1 (1) | +1 | 1d6+2 to 1d12+4 | 11 | 7 / 12 |
| 2 (2–4) | +2 | 2d6+3 to 2d12+4 | 14 | 10 / 20 |
| 3 (5–7) | +3 | 3d8+3 to 3d12+5 | 17 | 20 / 32 |
| 4 (8–10) | +4 | 4d8+10 to 4d12+15 | 20 | 25 / 45 |

These are benchmarks; adjust by type and fiction.

## Tier 1 Ranges by Type

Measured from the SRD's Tier 1 stat blocks (typical value in brackets):

| Type | Difficulty | HP | Stress | ATK | Thresholds | Standard attack damage |
|------|-----------|----|--------|-----|------------|------------------------|
| Minion | 8–11 (10) | 1 | 1 | −4 to +3 (−1) | none | flat 1–4 |
| Standard | 10–14 (12) | 2–5 (4) | 1–4 (3) | −1 to +2 (+1) | 4/6 to 8/14 | 1d6+2 to 1d10+1 |
| Support | ~13 | ~4 | ~4 | ~+2 | ~5/9 | low; relies on curses, conditions, resource denial |
| Skulk | 8–14 (12) | 2–5 (3) | 1–5 (3) | −2 to +2 (+1) | 3/– to 6/11 | 1d4+1 to 1d8+1, or 1d4+4 |
| Ranged | 9–13 (10) | 2–3 | 2 | −1 to +2 (+1) | 4/– to 4/8 | 1d6+3 to 1d8+3 |
| Bruiser | 10–14 (13) | 5–7 (6) | 2–4 (3) | −3 to +2 (+1) | 6/10 to 9/17 | 1d10+1 to 1d12+3, or flat-heavy like 1d4+6 |
| Leader | 11–15 (14) | 6–7 | 2–5 (3) | 0 to +4 (+3) | 6/11 to 8/14 | 1d8+3 to 1d10+4 |
| Horde | 8–12 (10) | 3–8 (6) | 2–4 (3) | −3 to +1 (−1) | 4/8 to 6/12 | 1d4+2 to 1d10+2 |
| Solo | 12–14 (13) | 6–10 (8) | 3–5 (3) | +1 to +4 (+3) | 7/14 to 8/15 | 1d10+2 to 2d8+4, or 1d20 |
| Social | 12–14 | 3 | 3–5 | −4 to −3 | 4/8 to 6/10 | 1d4+1 to 1d6+3 |

The SRD has only one Tier 1 Support (Jagged Knife Hexer), so its row is a single example. Some fragile Skulks and Ranged adversaries have no Severe threshold.

## Scaling Across Tiers

Typical values across all types, from the SRD stat blocks:
- **Stress:** about 3 at Tier 1, 4 at Tier 2, 5 at Tier 3, 5–8 at Tier 4. Solos and Leaders run highest.
- **HP:** grows slowly, roughly 4 → 4 → 7 → 6 for Standards and 8 → 9 → 10 → 11 for Solos. Minions always have 1.
- **Difficulty:** usually within ±2 of the tier benchmark; Skulks, Minions and Hordes lower, Leaders and Solos higher.
- **ATK:** lower when damage is high; Leaders often exceed the benchmark.

## Features (p. 93–94)

Three kinds plus Fear Features:
- **Passive:** always on, no cost.
- **Action:** used when the adversary has the spotlight.
- **Reaction:** fires on its trigger, even without the spotlight.
- **Fear Feature:** high-impact, costs Fear. It should change the scene, not just add damage.

Common SRD passives:
- **Minion (X):** defeated by any damage. For every X damage dealt, defeat another Minion within range.
- **Horde (X):** once half its HP is marked, its standard attack deals X instead.
- **Relentless (X):** can be spotlighted up to X times per GM turn (Fear as usual). Most Solos have it (44 of 51 in the SRD), so it's the default for a Solo but not a rule.
- **Slow:** must spend one spotlight preparing (place a token) before acting.

Adversary Stress is tracked individually and fuels stronger actions: area attacks, extra attacks, summons, mobility, forced conditions.

Useful patterns (house guidance, not SRD rules):
- Weak enemy: 1 passive + 1 action
- Mid enemy: 1 passive + 1 action + 1 reaction
- Solo: 2 passives + 2 actions + 1 reaction + 1 Fear Feature

Fast creation template: `[Type] + [Behavior] + [One twist]`, e.g. "Support + curse + Hope denial".

## Running Adversaries (p. 48, 51, 86)

When spotlighted, an adversary can: move within Close and make a standard attack; move within Close and use an action; clear a temporary condition; sprint within Far or Very Far; or anything else the fiction demands. Spend more Fear to spotlight more adversaries.

Attacks: GM rolls d20 + ATK against the PC's Evasion. A natural 20 succeeds and adds the damage dice's maximum to the damage.

## Building Encounters (p. 94)

Budget = **(3 × number of PCs) + 2** Battle Points. Adjust:
- −1 for an easier or shorter fight
- −2 if using two or more Solos
- −2 if adding +1d4 (or a static +2) to all adversary damage
- +1 if choosing an adversary from a lower tier
- +1 if there are no Bruisers, Hordes, Leaders or Solos
- +2 for a harder or longer fight

Costs:

| Cost | Adversary |
|------|-----------|
| 1 | A group of Minions equal in size to the party; each Social or Support |
| 2 | Each Horde, Ranged, Skulk or Standard |
| 3 | Each Leader |
| 4 | Each Bruiser |
| 5 | Each Solo |

## The SRD Is Not Rigid

Fiction first → type second → benchmark third. Say "Tier 1 Standard, HP 3–5 depending on fiction", not "Tier 1, therefore HP 4". A dramatically weaker or stronger adversary is right if the fiction justifies it.
