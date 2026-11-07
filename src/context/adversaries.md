# Daggerheart Adversaries — GM Context Module

## Core Principle

Build adversaries **fiction first, role second, numbers last**. Start from what the creature does narratively, assign a role, then apply tier-appropriate statistics. Adversaries should feel tactically distinct rather than mechanically overloaded.

## Stat Block Structure

Every adversary contains:
- Name, Tier, Role
- Short description + motives/tactics (3–5 verbs)
- Difficulty (target number against them)
- Thresholds: Major / Severe damage breakpoints
- HP, Stress, Attack modifier
- Standard attack: name, range, damage expression (e.g. `2d6+4 physical`)
- Features (2–4 total: passive / action / reaction / fear feature)
- Optional Experience: `Name +X` (used by spending Fear to raise attack rolls or Difficulty)

## Adversary Roles

| Role | Identity |
|------|----------|
| **Minion** | Dies from any damage; dangerous only in numbers |
| **Standard** | Baseline enemy; balanced and simple |
| **Bruiser** | High damage, durability, melee pressure |
| **Skulk** | Mobility, ambush, positional tricks |
| **Support** | Buffs allies, debuffs PCs, battlefield control |
| **Leader** | Commands, summons, manipulates spotlight |
| **Ranged** | Fragile but high pressure at distance |
| **Solo** | One enemy challenging the full party |
| **Horde** | Many creatures acting as one HP pool |
| **Social** | Conversation pressure instead of combat |

## Tier Benchmarks

| Tier | ATK Mod | Damage Range | Difficulty | Major / Severe |
|------|---------|--------------|------------|----------------|
| 1 | +1 | 1d6+2 to 1d12+4 | 11 | 7 / 12 |
| 2 | +2 | 2d6+3 to 2d12+4 | 14 | 10 / 20 |
| 3 | +3 | 3d8+3 to 3d12+5 | 17 | 20 / 32 |
| 4 | +4 | 4d8+10 to 4d12+15 | 20 | 25 / 45 |

These are benchmarks only. Adjust by role (see below).

## Tier 1 Role Templates

**Minion** — Difficulty 10–11, HP 1, Stress 1, ATK -4 to -1, Damage 1–2 flat, No thresholds. Include `Minion(X)` rule: excess damage defeats X additional nearby minions.

**Standard** — Difficulty 10–12, HP 3–5, Stress 2–3, ATK +0 to +1, Damage 1d6+1 to 1d8+2, Thresholds 4/8 to 8/14.

**Support** — Difficulty 12–13, HP 4, Stress 3–4, ATK +1 to +2, Damage 1d6+2, Thresholds ~5/9. Low direct damage; apply curses, conditions, or resource denial.

**Skulk** — Difficulty 12–14, HP 4–5, Stress 3–4, ATK +1 to +2, Damage 1d8+2. Movement tricks, traps, disengage tools; punishes exposed PCs.

**Bruiser** — Difficulty 12–14, HP 6–7, Stress 2–4, ATK +1 (lower if damage is high), Damage 1d8+3 to 1d12+2, Thresholds 7/13 to 9/17.

**Leader** — Difficulty 14–15, HP 6–7, Stress 3, ATK +3 to +4, Damage 1d8+4 to 1d10+4, Thresholds 7/13 to 8/14. Commands allies, spotlight manipulation; weakest when isolated.

**Horde** — Difficulty 10–12, HP 6, Stress 2–3, ATK -3 to 0, Damage 1d6+2 to 1d8+2, Thresholds 5/9 to 6/11. Damage output weakens at half HP.

**Solo** — Difficulty 13, HP 8–9, Stress 3–4, ATK +1 to +4, Damage 1d10+2 to 1d20, Thresholds 7/15 to 8/15. **Must have**: Relentless, multiple action types, phase logic or battlefield control.

## Role Scaling Across Tiers

**HP per tier increase:** Minion stays 1; Standard/Support/Skulk/Leader +1; Bruiser/Horde/Solo +1 to +2.

**Stress by tier:** Tier 1: 2–3 | Tier 2: 3–4 | Tier 3: 4–5 | Tier 4: 5–6.

**Difficulty drift:** Base benchmark ±2. Fragile/skulk/minion = lower end. Bruiser/leader/solo = higher end.

**ATK drift:** Low ATK if damage is high. High ATK if utility role. Leaders often exceed benchmark by +1.

## Feature Design

Most adversaries need only **2–4 features**:
- **Passive** — always on
- **Action** — special move when spotlighted
- **Reaction** — trigger-based response
- **Fear Feature** — high-impact move costing Fear (should alter scene direction)

Recommended patterns:
- Weak enemy: 1 passive + 1 action
- Mid enemy: 1 passive + 1 action + 1 reaction
- Boss/Solo: 2 passives + 2 actions + 1 reaction + 1 fear feature

## Stress Usage

Adversary stress fuels stronger actions. Spend for: AoE attacks, bonus attacks, summons, mobility spikes, forced conditions. Each adversary tracks stress individually.

## The Key Design Question

Every adversary must answer: **"What makes fighting this enemy different from any other?"**
If unclear, add one defining feature tied to the fiction. Never produce a stat block that is mechanically indistinct.

## Fast Creation Template

`[Role] + [Behavior] + [One twist]`

Examples:
- Bruiser + grapples + armor break
- Skulk + rooftops + trap reveal
- Support + curse + hope denial
- Leader + summons + forced ally spotlight

## Important: The SRD Is Not Rigid

Fiction first → role second → benchmark third.

Do not output: *"Tier 1 therefore exact HP 4"*
Do output: *"Tier 1 standard, HP 3–5 depending on fiction"*

Numbers serve the story. A dramatically weaker or stronger adversary is correct if the fiction justifies it.
