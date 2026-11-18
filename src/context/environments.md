# Daggerheart Environments — GM Context Module

Checked against the Daggerheart SRD 2.0 (2026-08-25): environments p. 158–159 and the tier 1 stat blocks that follow; countdowns from the GM chapter.

## Core Principle

An environment is **everything in a scene beyond the PCs and adversaries**: the physical space, background NPCs, natural forces, or an event in progress. Its features are **GM moves waiting to happen**: ways the place pushes and pulls the people in it. Each feature comes with questions that turn the move into story. An environment without impulses is scenery; one with them is an opponent, an ally, or a temptation.

## Stat Block Structure (p. 158)

- **Name, Tier, Type.**
- **Description:** one evocative line.
- **Impulses:** 2–3 short phrases for how the environment pushes and pulls ("Draw in the curious, echo the past").
- **Difficulty:** the standard Difficulty for action rolls made to overcome, oppose or resist the environment or its elements. It can be `Special` when a feature sets it (e.g. "equals that of the adversary with the highest Difficulty").
- **Potential Adversaries:** suggested adversaries that might appear here, often grouped under a fictional label ("Grove Guardians (Minor Treant, Sylvan Soldier, Young Dryad)"), or `Any`.
- **Features:** written `Name - Kind: effect`, where Kind is Passive, Action or Reaction, like adversary features. Fear Features cost Fear. **Each feature is followed by one or two feature questions in italics**: prompts for plot hooks, narrative engines and connections ("What are the grove guardians concealing?").

## Environment Types (p. 158)

| Type | The scene it supports |
|------|-----------------------|
| **Exploration** | Wondrous locations with mysteries and marvels to discover |
| **Social** | Locations that primarily present interpersonal challenges |
| **Traversal** | Dangerous locations where moving through and around the space is itself the challenge |
| **Event** | Special activities or occurrences rather than physical spaces (an ambush, a festival, a trial) |

## Benchmarks by Tier (p. 159)

| Statistic | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|-----------|--------|--------|--------|--------|
| Difficulty | 11 | 14 | 17 | 20 |
| Damage dice | 1d6+1 to 1d8+3 | 2d6+3 to 2d10+2 | 3d8+3 to 3d10+1 | 4d8+3 to 4d10+10 |

Tier-1 stat blocks in the SRD range from Difficulty 10 (a bustling marketplace) to 12 (a cliffside ascent): the benchmark is a centre, not a rule. To retune an environment to another tier, swap in that column's numbers.

## Battle Points

Environments cost **no Battle Points**. The encounter budget buys adversaries only. A scene can be an environment alone (a river crossing, a festival) with no budget at all.

## Writing Features

Patterns from the SRD stat blocks:
- **A Passive that defines the scene's core challenge or opportunity**, often a roll the PCs can make and what each outcome reveals or costs (an Instinct Roll to read an old battlefield: three facts on success with Hope, two with Fear, one for a Stress on failure).
- **An Action that shifts the scene:** hazards that grab, collapse, flood or burn (an Agility Reaction Roll or take tier damage and become Restrained), reinforcements arriving, a revelation.
- **A Fear-paid Action or Reaction for the dramatic turn:** "Spend a Fear to…" summon something, turn the terrain hostile, or separate the party.
- **Countdowns for anything that builds:** a storm gathering, a climb, a chase, a ritual nearing completion.
- **Social and Event environments** use features for leverage and complications: a bribe that grants advantage, a merchant who has exactly what someone needs, a thief, a rival's arrival.
- **3–4 features per environment.** Every feature names its mechanics plainly enough to run at the table, then asks its questions.

Example:
```
CLIFFSIDE ASCENT — Tier 1 Traversal
A steep, rocky cliffside tall enough to make traversal dangerous.
Impulses: Cast the unready down to a rocky doom, draw people in with promise of what lies at the top
Difficulty: 12
Potential Adversaries: Construct, Deeproot Defender, Giant Scorpion, Glass Snake
The Climb - Passive: Climbing the cliffside uses a Progress Countdown (12). When the countdown triggers, the party has made it to the top.
  What strange formations are the stones arranged in? What ominous warnings did previous adventurers leave?
Pitons Left Behind - Passive: If a PC using the pitons fails an action roll to climb, they can mark a Stress instead of ticking the countdown up.
  What do the shape and material of these pitons tell you about the previous climbers?
Fall - Action: Spend a Fear to have a PC's handhold fail. If they aren't saved on the next action, they hit the ground, tick the countdown up by 2, and take 1d12 physical damage (2d12 if the countdown is 4–7, 3d12 at 3 or lower).
  How can you tell many others have fallen here before?
```

## Countdowns

A countdown starts at a value and triggers its effect when it reaches 0.
- **Standard countdown** ("Countdown (n)"): advances by 1 every time a player makes an action roll.
- **Dynamic countdowns** advance by up to 3 depending on the roll. **Progress** countdowns lead to good outcomes, **Consequence** countdowns to bad ones:

| Roll result | Progress | Consequence |
|-------------|----------|-------------|
| Failure with Fear | no advancement | tick down 3 |
| Failure with Hope | no advancement | tick down 2 |
| Success with Fear | tick down 1 | tick down 1 |
| Success with Hope | tick down 2 | no advancement |
| Critical Success | tick down 3 | no advancement |

- **Variants:** random starting values; looping countdowns (reset after triggering, optionally growing or shrinking by 1 each loop); linked progress and consequence countdowns racing on the same rolls (a chase: catch the thief before they reach their hideout); long-term countdowns that advance on rests instead of rolls.
