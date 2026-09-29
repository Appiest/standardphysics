# D to C: tier 1 rule review sheet

## Why this has to happen tonight

The ledger at `packages/agents/standardphysics_agents/rules/data/verification.json` is `{"entries": []}`. `run_checks` only runs a rule the ledger verifies (`checks/__init__.py:97-113`, `pack.py:127-129`), so every scan comes back with no findings until a person records entries. An entry binds the rule id, section, threshold and unit (`verification.py:51-59`). If any of those four changes in the pack, the rule switches itself off again.

| | |
|---|---|
| Who reads | One reviewer reads each section and types the number back. PLAN section 8: "Every threshold is verified against primary source text by a human, and a second person checks the citation." |
| Who checks | A second, different person opens the same links, confirms the section number and the quote, and records a second check. |
| Rules | 14 tier 1 rules. 13 get recorded tonight and 1 is held. |
| Time | This is an estimate. Each rule is one quote to compare with one linked paragraph, so plan on about an hour for the reader and half an hour for the second person. |

Paths below are shortened. `agents/` is `packages/agents/standardphysics_agents/`, `checks/` is `packages/agents/standardphysics_agents/checks/`, and `pipeline/` is `packages/pipeline/standardphysics_pipeline/`. Code line numbers are at `a98cefd`. Nothing under `packages/` changed between `256ced3` and `a98cefd`.

## Commands

Run everything from the repo root of the checkout that will be committed.

**1. Confirm which ledger file the command writes.**

```sh
.venv/bin/python -c "from standardphysics_agents.rules import ledger_path; print(ledger_path())"
```

It must print this checkout's `packages/agents/standardphysics_agents/rules/data/verification.json`. A virtualenv installed from a different clone writes to that clone's ledger instead. I hit this while preparing the sheet. If `STANDARDPHYSICS_VERIFICATION_LEDGER` is set, that path wins (`verification.py:116-120`). If the path is wrong, put this checkout first on the path:

```sh
PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents .venv/bin/python -m standardphysics_agents.cli rules list --tier 1
```

**2. Read one rule the way the CLI shows it.**

```sh
.venv/bin/standardphysics-agents rules show route_clear_width
```

**3. Record a verification.** Copied from `cli.py:314-323`.

```sh
.venv/bin/standardphysics-agents rules verify route_clear_width --by "Your Name" --note "Read 403.5.1 at access-board.gov/ada/#ada-403_5_1"
```

The command prints the rule, then asks `Type the number you read in 403.5.1:`. Type the number from the source, as a decimal. The CLI parses the text as a float (`cli.py:145-149`), so `1/2` is rejected and `0.5` works.

| Result | Output |
|---|---|
| Right number | `route_clear_width is on. 36 in, read by Your Name.` |
| Wrong number | `That is not the threshold in the pack. route_clear_width stays off.` and exit code 1 |

`--threshold 36` skips the prompt. Typing the number is the confirmation the CLI was built around, so prefer the prompt.

**4. Record the second check.** Copied from `cli.py:329-331`.

```sh
.venv/bin/standardphysics-agents rules second-check route_clear_width --by "Second Name"
```

It prints `route_clear_width has two readers.` If nobody has verified the rule yet, it stops with `KeyError: 'route_clear_width has no first verification to check'`.

**5. Confirm the ledger changed.**

```sh
.venv/bin/standardphysics-agents rules list --tier 1
git diff packages/agents/standardphysics_agents/rules/data/verification.json
```

The last column of `rules list` moves from `waiting on a person` to `verified by Your Name, wants a second reader`, then to `verified by Your Name, checked by Second Name`. The diff shows one entry per rule with this shape. I ran it against a scratch ledger to get it:

```json
{
  "rule_id": "door_hardware",
  "section": "404.2.7",
  "threshold": 48.0,
  "unit": "in",
  "verified_by": "Your Name",
  "verified_at": "2026-09-13T03:14:57.477427Z",
  "second_check_by": "Second Name",
  "second_check_at": "2026-09-13T03:14:58.216266Z",
  "note": "read 404.2.7 on access-board.gov"
}
```

**Things to know before starting**

- **Order.** Run `second-check` after the last `verify` of a rule. `verify` replaces the whole entry, which drops an earlier second check (`verification.py:80-92`).
- **Walking the list.** `rules review --by "Your Name"` walks every unverified tier 1 rule in pack order (`cli.py:115-142`). A blank line stops the walk. At a Hold rule, type `skip`: any text that isn't a number leaves that rule off and moves to the next one. `review` takes no `--note`.
- **Tier 1 only.** Only tier 1 rules run in the API today. `Stages.assess` calls `assess` without `max_tier` (`services/api/standardphysics_api/stages.py:75-85`), and `assess` defaults to `max_tier=1` (`agents/assess.py:69`). Tier 2 and 3 rules need no reader tonight. Rules whose `applies_to` names no route subject run as soon as a scan is ingested. Route rules wait for a confirmed route (`stages.py:32`, `stages.py:78-79`).
- **When entries take effect.** The API reads the ledger file at every assessment (`stages.py:77`), so a new entry applies to the next one. Another machine sees the entries only after `verification.json` is committed and pulled.
- **Preview mode.** `SP_PREVIEW_UNVERIFIED_RULES=1` ignores the ledger file and treats every rule as read (`services/api/standardphysics_api/app.py:76`). Leave it unset to see what the ledger turns on.

## Verdicts

| Verdict | Meaning |
|---|---|
| Verify | The pack's section, number and quote match the 2010 Standards, and the check or question applies them the way the section does. Record it. |
| Verify, fix logic | The pack's section, number and quote match, so recording it is correct. The check applies the number in a way the section does not, and the named lane owns the fix. A code fix leaves the ledger entry valid, because the entry binds only id, section, threshold and unit. |
| Hold | The pack entry itself is not right yet. Either no section is pinned, or the pack's quote is not in the source. Verifying now would bind the ledger to a section or quote that has to change. |

Every 2010 Standards quote below was copied from the DOJ page, [ada.gov 2010 Standards](https://www.ada.gov/law-and-regs/design-standards/2010-stds/). Each one was checked against the same section on the [Access Board page](https://www.access-board.gov/ada/), which has the same wording and a link to each subsection.

## Ready to verify

### `entrance_threshold`

- **Rule id:** `entrance_threshold`
- **Citation in pack:** ADA 2010, 303.3
- **Threshold in pack:** 0.5 in, at most. The parameters are `vertical_max_inches` 0.25, `beveled_max_inches` 0.5 and `bevel_max_slope_run_per_rise` 2. Type `0.5`.
- **Pack source_text:**
  > 303.2 Vertical. Changes in level of 1/4 inch (6.4 mm) high maximum shall be permitted to be vertical. 303.3 Beveled. Changes in level between 1/4 inch (6.4 mm) high minimum and 1/2 inch (13 mm) high maximum shall be beveled with a slope not steeper than 1:2.
- **Primary source:** [2010 Standards 303](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#303-changes-in-level) and [Access Board 303.3](https://www.access-board.gov/ada/#ada-303_3)
  > 303.2 Vertical. Changes in level of 1/4 inch (6.4 mm) high maximum shall be permitted to be vertical.
  >
  > 303.3 Beveled. Changes in level between 1/4 inch (6.4 mm) high minimum and 1/2 inch (13 mm) high maximum shall be beveled with a slope not steeper than 1:2.

  [Access Board 404.2.5](https://www.access-board.gov/ada/#ada-404_2_5), the doorway section:
  > 404.2.5 Thresholds. Thresholds, if provided at doorways, shall be 1/2 inch (13 mm) high maximum. Raised thresholds and changes in level at doorways shall comply with 302 and 303.
- **Match:** Yes. Both sentences match word for word, and 0.5 in is the 303.3 maximum.
- **What the check does:** `checks/questions.py:38` attaches the entrance door. Because the rule's evidence is a photo, `agents/findings.py:42-43` always makes it a question, so the threshold never decides a pass or a fail. The owner reads `agents/copy.py:244-247`, which writes "the half inch" itself instead of reading the pack.
- **Note:** 404.2.5 covers doorways directly and sets the same 1/2 inch. If Lane C moves the citation to 404.2.5, this rule has to be verified again.
- **Verdict:** Verify

### `door_hardware`

- **Rule id:** `door_hardware`
- **Citation in pack:** ADA 2010, 404.2.7
- **Threshold in pack:** 48 in, at most. The parameters are `min_height_inches` 34 and `max_height_inches` 48. Type `48`.
- **Pack source_text:**
  > 404.2.7 Door and Gate Hardware. Handles, pulls, latches, locks, and other operable parts on doors and gates shall comply with 309.4. Operable parts of such hardware shall be 34 inches (865 mm) minimum and 48 inches (1220 mm) maximum above the finish floor. 309.4 requires operation with one hand and without tight grasping, pinching, or twisting of the wrist.
- **Primary source:** [2010 Standards 404](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#404-doors-doorways-and-gates), [Access Board 404.2.7](https://www.access-board.gov/ada/#ada-404_2_7) and [Access Board 309.4](https://www.access-board.gov/ada/#ada-309_4)
  > 404.2.7 Door and Gate Hardware. Handles, pulls, latches, locks, and other operable parts on doors and gates shall comply with 309.4. Operable parts of such hardware shall be 34 inches (865 mm) minimum and 48 inches (1220 mm) maximum above the finish floor or ground. Where sliding doors are in the fully open position, operating hardware shall be exposed and usable from both sides.
  >
  > 309.4 Operation. Operable parts shall be operable with one hand and shall not require tight grasping, pinching, or twisting of the wrist. The force required to activate operable parts shall be 5 pounds (22.2 N) maximum.
- **Match:** Yes on 34 and 48 in. The pack cuts "or ground" and the sliding-door sentence, and it leaves out both exceptions. Its last sentence paraphrases 309.4 and drops the 5 pound activating force.
- **What the check does:** `checks/questions.py:39` asks about the entrance door. The question is always a question (`agents/findings.py:42-43`). The owner reads `agents/copy.py:248-251`, which writes "between 34 and 48 inches up" itself.
- **Verdict:** Verify

### `floor_surface`

- **Rule id:** `floor_surface`
- **Citation in pack:** ADA 2010, 302
- **Threshold in pack:** 0.5 in, at most. The parameters are `carpet_pile_max_inches` 0.5 and `opening_sphere_max_inches` 0.5. Type `0.5`.
- **Pack source_text:**
  > 302.1 General. Floor and ground surfaces shall be stable, firm, and slip resistant. 302.2 Carpet. Carpet or carpet tile shall be securely attached and shall have a firm cushion, pad, or backing or no cushion or pad. Carpet or carpet tile shall have a level loop, textured loop, level cut pile, or level cut/uncut pile texture. Pile height shall be 1/2 inch (13 mm) maximum. 302.3 Openings. Openings in floor or ground surfaces shall not allow passage of a sphere more than 1/2 inch (13 mm) diameter.
- **Primary source:** [2010 Standards 302](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#302-floor-or-ground-surfaces), [Access Board 302.2](https://www.access-board.gov/ada/#ada-302_2) and [302.3](https://www.access-board.gov/ada/#ada-302_3)
  > 302.1 General. Floor and ground surfaces shall be stable, firm, and slip resistant and shall comply with 302.
  >
  > 302.2 Carpet. Carpet or carpet tile shall be securely attached and shall have a firm cushion, pad, or backing or no cushion or pad. Carpet or carpet tile shall have a level loop, textured loop, level cut pile, or level cut/uncut pile texture. Pile height shall be 1/2 inch (13 mm) maximum. Exposed edges of carpet shall be fastened to floor surfaces and shall have trim on the entire length of the exposed edge. Carpet edge trim shall comply with 303.
  >
  > 302.3 Openings. Openings in floor or ground surfaces shall not allow passage of a sphere more than 1/2 inch (13 mm) diameter except as allowed in 407.4.3, 409.4.3, 410.4, 810.5.3 and 810.10. Elongated openings shall be placed so that the long dimension is perpendicular to the dominant direction of travel.
- **Match:** Yes on both 1/2 inch limits. The pack shortens 302.1 and 302.3 and drops the carpet edge sentences.
- **What the check does:** `checks/questions.py:41` attaches the first floor node, and the question is always a question. The owner reads `agents/copy.py:256-259`, which asks about mats and carpet pile but not about openings such as floor grates.
- **Verdict:** Verify

### `restroom_turning_space`

- **Rule id:** `restroom_turning_space`
- **Citation in pack:** ADA 2010, 603.2.1
- **Threshold in pack:** 60 in, at least. The parameters are `water_closet_centerline_min_inches` 16 and `water_closet_centerline_max_inches` 18. Type `60`. The prompt names 603.2.1, but the number is in 304.3.1.
- **Pack source_text:**
  > 603.2.1 Turning Space. Turning space complying with 304 shall be provided within the room. 604.2 Location. The water closet shall be positioned with a wall or partition to the rear and to one side. The centerline of the water closet shall be 16 inches (405 mm) minimum to 18 inches (455 mm) maximum from the side wall or partition.
- **Primary source:** [2010 Standards 603](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#603-toilet-and-bathing-rooms), [Access Board 603.2.1](https://www.access-board.gov/ada/#ada-603_2_1), [304.3](https://www.access-board.gov/ada/#ada-304_3) and [604.2](https://www.access-board.gov/ada/#ada-604_2)
  > 603.2.1 Turning Space. Turning space complying with 304 shall be provided within the room.
  >
  > 304.3 Size. Turning space shall comply with 304.3.1 or 304.3.2.
  >
  > 304.3.1 Circular Space. The turning space shall be a space of 60 inches (1525 mm) diameter minimum. The space shall be permitted to include knee and toe clearance complying with 306.
  >
  > 604.2 Location. The water closet shall be positioned with a wall or partition to the rear and to one side. The centerline of the water closet shall be 16 inches (405 mm) minimum to 18 inches (455 mm) maximum from the side wall or partition, except that the water closet shall be 17 inches (430 mm) minimum and 19 inches (485 mm) maximum from the side wall or partition in the ambulatory accessible toilet compartment specified in 604.8.2. Water closets shall be arranged for a left-hand or right-hand approach.
- **Match:** Yes. 603.2.1 points to 304, and 304.3.1 sets 60 inches. The pack's 604.2 sentence stops before the ambulatory compartment clause.
- **What the check does:** `checks/questions.py:42` asks on every scan with no node attached, and the question is always a question. The owner reads `agents/copy.py:260-263`, which asks for "the customer restroom" and a "60 inch circle". That copy leaves out the T-shaped space that 304.3.2 also allows. PLAN section 8 scopes this to "the restroom (603, 604) when customers use it", but nothing checks whether a shop has one.
- **Verdict:** Verify

## Verify with a logic fix

### `route_clear_width`

- **Rule id:** `route_clear_width`
- **Citation in pack:** ADA 2010, 403.5.1
- **Threshold in pack:** 36 in, at least. The parameters are `reduced_min_inches` 32, `reduced_max_run_inches` 24, `separating_segment_min_length_inches` 48 and `separating_segment_min_width_inches` 36. Type `36`.
- **Pack source_text:**
  > Except as provided in 403.5.2 and 403.5.3, the clear width of walking surfaces shall be 36 inches (915 mm) minimum. EXCEPTION: The clear width shall be permitted to be reduced to 32 inches (815 mm) minimum for a length of 24 inches (610 mm) maximum provided that reduced width segments are separated by segments that are 48 inches (1220 mm) minimum in length and 36 inches (915 mm) minimum in width.
- **Primary source:** [2010 Standards 403](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#403-walking-surfaces) and [Access Board 403.5.1](https://www.access-board.gov/ada/#ada-403_5_1)
  > 403.5.1 Clear Width. Except as provided in 403.5.2 and 403.5.3, the clear width of walking surfaces shall be 36 inches (915 mm) minimum.
  >
  > EXCEPTION: The clear width shall be permitted to be reduced to 32 inches (815 mm) minimum for a length of 24 inches (610 mm) maximum provided that reduced width segments are separated by segments that are 48 inches (1220 mm) long minimum and 36 inches (915 mm) wide minimum.
- **Match:** Yes on every number. The pack rewords the last clause: "minimum in length" and "minimum in width" stand where the source says "long minimum" and "wide minimum".
- **What the check does:**
  - `checks/route_width.py:31-43` accepts a width between 32 and 36 in when the longest narrow run on the leg is 24 in or less. Nothing reads the two `separating_segment_*` parameters. A leg with two 24 in runs at 32 in, separated by 10 in of 36 in route, therefore passes. The exception needs 48 in between them.
  - `pipeline/routes.py:37` and `routes.py:98-123` leave out every grid cell within 0.75 m of each stop. The radius is capped at 35 percent of the stop-to-stop distance. Those cells count toward neither the width (`routes.py:154-157`) nor the run length (`routes.py:271-275`). Audit A-6 is still open: a 20 in gap 0.2 m inside the entrance reads 31 in. Its pinned test still fails as expected at HEAD.
- **Fix owner:** Lane C for the separation condition. Lane B for the endpoint exemption.
- **Verdict:** Verify, fix logic

### `turn_clear_width`

- **Rule id:** `turn_clear_width`
- **Citation in pack:** ADA 2010, 403.5.2
- **Threshold in pack:** 48 in, at least. The parameters are `element_width_below_inches` 48, `approaching_min_inches` 42, `at_turn_min_inches` 48, `leaving_min_inches` 42 and `exempt_at_turn_width_inches` 60. Type `48`.
- **Pack source_text:**
  > Where the accessible route makes a 180 degree turn around an element which is less than 48 inches (1220 mm) in width, clear width shall be 42 inches (1065 mm) minimum approaching the turn, 48 inches (1220 mm) minimum at the turn and 42 inches (1065 mm) minimum leaving the turn. EXCEPTION: 403.5.2 shall not apply where the clear width at the turn is 60 inches (1525 mm) minimum.
- **Primary source:** [2010 Standards 403](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#403-walking-surfaces) and [Access Board 403.5.2](https://www.access-board.gov/ada/#ada-403_5_2)
  > 403.5.2 Clear Width at Turn. Where the accessible route makes a 180 degree turn around an element which is less than 48 inches (1220 mm) wide, clear width shall be 42 inches (1065 mm) minimum approaching the turn, 48 inches (1220 mm) minimum at the turn and 42 inches (1065 mm) minimum leaving the turn.
  >
  > EXCEPTION: Where the clear width at the turn is 60 inches (1525 mm) minimum compliance with 403.5.2 shall not be required.
- **Match:** Yes on every number. The pack writes "in width" for "wide" and rewords the exception without changing its meaning.
- **What the check does:** `checks/turn_width.py:52-81` applies the section's numbers and its exception correctly. The widths come from Lane B's `pipeline/turns.py` through `pipeline/measure.py:266-293`, and audits A-14 and A-15 are still open. I reran the audit's two test rooms at HEAD:
  - A turn built with 43 in lanes and 49 in at the turn reads approaching None, 43.31 in at the turn and 43.31 in leaving.
  - A turn built with 61 in at the turn reads 35.43 in there, so the 60 in exception can never apply.

  When any zone reads None, the check tells the team and shows the owner nothing (`turn_width.py:139-141`). That is what happens on both rooms today. When all three zones do measure, they read narrow, so expect false "too tight" cards until A-14 is fixed. An element whose width is unknown counts as 48 in or wider, so the rule is skipped for it (`turn_width.py:106-112`).
- **Fix owner:** Lane B (`turns.py`, A-14 and A-15)
- **Verdict:** Verify, fix logic

### `passing_space`

- **Rule id:** `passing_space`
- **Citation in pack:** ADA 2010, 403.5.3
- **Threshold in pack:** 60 in, at least. The parameters are `applies_below_route_width_inches` 60, `space_min_inches` 60, `interval_max_feet` 200 and `t_arm_extension_min_inches` 48. Type `60`.
- **Pack source_text:**
  > An accessible route with a clear width less than 60 inches (1525 mm) shall provide passing spaces at intervals of 200 feet (61 m) maximum. Passing spaces shall be either: a space 60 inches (1525 mm) minimum by 60 inches (1525 mm) minimum; or, an intersection of two walking surfaces providing a T-shaped space complying with 304.3.2 where the base and arms of the T-shaped space extend 48 inches (1220 mm) minimum beyond the intersection.
- **Primary source:** [2010 Standards 403](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#403-walking-surfaces) and [Access Board 403.5.3](https://www.access-board.gov/ada/#ada-403_5_3)
  > 403.5.3 Passing Spaces. An accessible route with a clear width less than 60 inches (1525 mm) shall provide passing spaces at intervals of 200 feet (61 m) maximum. Passing spaces shall be either: a space 60 inches (1525 mm) minimum by 60 inches (1525 mm) minimum; or, an intersection of two walking surfaces providing a T-shaped space complying with 304.3.2 where the base and arms of the T-shaped space extend 48 inches (1220 mm) minimum beyond the intersection.
- **Match:** Yes, word for word.
- **What the check does:**
  - `checks/passing_space.py:94-103` samples the route every 0.5 m. `pipeline/measure.py:295-309` returns the largest clear circle at each sample point and reports its diameter as both width and depth. `checks/clear_floor.py:26-31` then accepts a 60 in diameter as the 60 by 60 in square. A 60 in circle cannot hold that square, because the square's corners sit about 42 in from its centre (60 × √2 ÷ 2). A round clearing between 60 and about 85 in across passes even though no 60 by 60 in square fits in it.
  - Nothing reads `t_arm_extension_min_inches`, so the T-shaped option is never tested. That gap makes the check stricter than the section.
  - `passing_space.py:66` counts sample points rather than separate spaces against the 200 ft interval. A shop route is shorter than 200 ft, so today one space is always enough.
  - `measure.py:308` also hard-codes `diameter >= 60.0`, which is a second copy of the pack's 60.
- **Fix owner:** Lane B for a square test on the measurement side (`D-to-B.md`). Lane C for the T-shaped option.
- **Verdict:** Verify, fix logic

### `turning_space`

- **Rule id:** `turning_space`
- **Citation in pack:** ADA 2010, 304.3.1
- **Threshold in pack:** 60 in, at least. The parameters are `circle_diameter_inches` 60, `t_square_inches` 60 and `t_arm_width_inches` 36. Type `60`.
- **Pack source_text:**
  > 304.3 Size. Turning space shall comply with 304.3.1 or 304.3.2. 304.3.1 Circular Space. The turning space shall be a space of 60 inches (1525 mm) diameter minimum. The space shall be permitted to include knee and toe clearance complying with 306.
- **Primary source:** [2010 Standards 304](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#304-turning-space), [Access Board 304.3](https://www.access-board.gov/ada/#ada-304_3) and [304.3.1](https://www.access-board.gov/ada/#ada-304_3_1)
  > 304.3 Size. Turning space shall comply with 304.3.1 or 304.3.2.
  >
  > 304.3.1 Circular Space. The turning space shall be a space of 60 inches (1525 mm) diameter minimum. The space shall be permitted to include knee and toe clearance complying with 306.

  [Access Board guide, Chapter 3: Clear Floor or Ground Space and Turning Space](https://www.access-board.gov/ada/guides/chapter-3-clear-floor-or-ground-space-and-turning-space/):
  > Recommendation: Turning space is recommended in small spaces with entrapment risks as well as at dead-end aisles and corridors so that people using wheeled mobility aids do not have to back up considerable distances.
  >
  > Is turning space required in all rooms and spaces? No. Turning space is required in certain spaces, such as toilet and bathing facilities, dressing and fitting rooms, and transient lodging guest rooms. Unless addressed by a specific requirement for turning space in the standards, other spaces are not required to provide them, including lobbies, offices, and meeting rooms.
- **Match:** Yes, word for word.
- **What the check does:** `checks/turning_space.py:28-31` runs at every stop the route doubles back out of (`checks/route_geometry.py:54-64`). It tests one 60 in circle centred 30 in back from that stop (`turning_space.py:36-38`), and `agents/findings.py:49` turns a miss into a problem citing 304.3.1. The guide lists dead ends only as a recommendation, so these cards cite a requirement the Standards do not place there. The check never tests the T-shaped space in 304.3.2. It also misses a circle that would fit just beside the one test point.
- **Fix owner:** Lane C. Make the dead-end result a question or a recommendation, or scope the rule to a room that requires turning space. The section and the number stay the same, so the ledger entry survives the fix.
- **Verdict:** Verify, fix logic

### `door_clear_width`

- **Rule id:** `door_clear_width`
- **Citation in pack:** ADA 2010, 404.2.3
- **Threshold in pack:** 32 in, at least. The parameter is `measured_at_door_angle_degrees` 90. Type `32`.
- **Pack source_text:**
  > Door openings shall provide a clear width of 32 inches (815 mm) minimum. Clear openings of doorways with swinging doors shall be measured between the face of the door and the stop, with the door open 90 degrees.
- **Primary source:** [2010 Standards 404](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#404-doors-doorways-and-gates) and [Access Board 404.2.3](https://www.access-board.gov/ada/#ada-404_2_3)
  > 404.2.3 Clear Width. Door openings shall provide a clear width of 32 inches (815 mm) minimum. Clear openings of doorways with swinging doors shall be measured between the face of the door and the stop, with the door open 90 degrees. Openings more than 24 inches (610 mm) deep shall provide a clear opening of 36 inches (915 mm) minimum. There shall be no projections into the required clear opening width lower than 34 inches (865 mm) above the finish floor or ground. Projections into the clear opening width between 34 inches (865 mm) and 80 inches (2030 mm) above the finish floor or ground shall not exceed 4 inches (100 mm).
- **Match:** Yes, word for word, for the two sentences the pack quotes. The pack leaves out the 36 in rule for openings more than 24 in deep, the projection limits and both exceptions.
- **What the check does:** `pipeline/measure.py:324` returns `max(door.dimensions.x, door.dimensions.y)`, which is the door's size in the wall. The function never sets `needs_measurement`, even though its docstring says it does (`measure.py:319-321`). `checks/door_width.py:37` then scores that number as the clear width. On the sample shop the owner reads "The front door is wide enough. It's 35.4 inches clear." The door node is 35.43 in across, and the clear width measured to the stop with the door open 90 degrees is narrower than that. Lane B held the flag in `B-to-C.md` ("A-9 is held, and it is a question for you") because setting it failed 15 Lane C tests and turned every shop into `RESCAN_AREA`.
- **Fix owner:** Lane C picks one of the options in `B-to-C.md`, and then Lane B sets the flag.
- **Verdict:** Verify, fix logic

### `service_counter_height`

- **Rule id:** `service_counter_height`
- **Citation in pack:** ADA 2010, 904.4.1
- **Threshold in pack:** 36 in, at most. The parameter is `accessible_length_min_inches` 36. Type `36`.
- **Pack source_text:**
  > 904.4.1 Parallel Approach. A portion of the counter surface that is 36 inches (915 mm) long minimum and 36 inches (915 mm) high maximum above the finish floor shall be provided. A clear floor or ground space complying with 305 shall be positioned for a parallel approach adjacent to the 36 inch (915 mm) minimum length of counter.
- **Primary source:** [2010 Standards 904](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#904-check-out-aisles-and-sales-and-service-counters), [Access Board 904.4](https://www.access-board.gov/ada/#ada-904_4), [904.4.1](https://www.access-board.gov/ada/#ada-904_4_1), [904.4.2](https://www.access-board.gov/ada/#ada-904_4_2) and [227.3](https://www.access-board.gov/ada/#ada-227_3)
  > 904.4 Sales and Service Counters. Sales counters and service counters shall comply with 904.4.1 or 904.4.2. The accessible portion of the counter top shall extend the same depth as the sales or service counter top.
  >
  > 904.4.1 Parallel Approach. A portion of the counter surface that is 36 inches (915 mm) long minimum and 36 inches (915 mm) high maximum above the finish floor shall be provided. A clear floor or ground space complying with 305 shall be positioned for a parallel approach adjacent to the 36 inch (915 mm) minimum length of counter.
  >
  > EXCEPTION: Where the provided counter surface is less than 36 inches (915 mm) long, the entire counter surface shall be 36 inches (915 mm) high maximum above the finish floor.
  >
  > 904.4.2 Forward Approach. A portion of the counter surface that is 30 inches (760 mm) long minimum and 36 inches (915 mm) high maximum shall be provided. Knee and toe space complying with 306 shall be provided under the counter. A clear floor or ground space complying with 305 shall be positioned for a forward approach to the counter.
  >
  > 227.3 Counters. Where provided, at least one of each type of sales counter and service counter shall comply with 904.4. Where counters are dispersed throughout the building or facility, counters complying with 904.4 also shall be dispersed.
- **Match:** Yes, word for word, for the two sentences the pack quotes. The pack leaves out the exception for counters under 36 in long.
- **What the check does:** `checks/service_counter.py:31-44` looks for a separate object that touches the counter, carries a lowered-section label (`checks/roles.py:23-31`), stands at most 36 in high and is at least 36 in long. If it finds one, it measures that object (`service_counter.py:61-63`). If not, it measures the top of the whole counter box (`pipeline/measure.py:331-338`). That branch matches the exception for short counters. The mismatches are:
  - A counter scanned as one box with a lowered part built in reads at its highest point and fails.
  - A 30 in forward-approach portion with knee and toe space (904.4.2) is not recognised, so a counter that complies that way fails.
  - The check reports every counter labelled as a service counter. 227.3 requires "at least one of each type".
  - Nothing checks that the portion extends the full depth of the counter top, which 904.4 requires.
- **Fix owner:** Lane C
- **Verdict:** Verify, fix logic

### `service_counter_approach`

- **Rule id:** `service_counter_approach`
- **Citation in pack:** ADA 2010, 305.3
- **Threshold in pack:** 48 in, at least. The parameters are `clear_width_min_inches` 48 and `clear_depth_min_inches` 30. Type `48`.
- **Pack source_text:**
  > 305.3 Size. The clear floor or ground space shall be 30 inches (760 mm) minimum by 48 inches (1220 mm) minimum. Referenced by 904.4.1, which positions that space for a parallel approach adjacent to the 36 inch minimum length of counter.
- **Primary source:** [2010 Standards 305](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#305-clear-floor-or-ground-space) and [Access Board 305.3](https://www.access-board.gov/ada/#ada-305_3)
  > 305.3 Size. The clear floor or ground space shall be 30 inches (760 mm) minimum by 48 inches (1220 mm) minimum.

  904.4.1 and 904.4.2 are quoted under `service_counter_height`.
- **Match:** Yes, word for word, for 305.3. The pack's second sentence is its own summary of 904.4.1, not a quote, and the summary is accurate.
- **What the check does:** `checks/service_counter.py:88-94` measures in front of each labelled service counter. `pipeline/measure.py:356-372` centres the 48 by 30 in rectangle on the middle of that counter's front face, with the 48 in side running along the counter. 904.4.1 places the space "adjacent to the 36 inch (915 mm) minimum length of counter" instead. On the sample shop the rectangle is centred on the high counter at x = 0.46 m, while the lowered section's centre is at x = -1.14 m. The check passes there with "96.5 by 60 inches". It never tests a forward approach under 904.4.2. `measure.py:47-48` also hard-codes 48 and 30 in for `fits`, which is a second copy of the pack's numbers.
- **Fix owner:** Lane B to measure at the portion. Lane C for the forward approach.
- **Verdict:** Verify, fix logic

### `door_opening_force`

- **Rule id:** `door_opening_force`
- **Citation in pack:** ADA 2010, 404.2.9
- **Threshold in pack:** 5 lbf, at most. The parameters are `interior_hinged_max_lbf` 5 and `sliding_or_folding_max_lbf` 5. Type `5`.
- **Pack source_text:**
  > 404.2.9 Door and Gate Opening Force. Fire doors shall have a minimum opening force allowable by the appropriate administrative authority. The force for pushing or pulling open a door or gate other than fire doors shall be as follows: (a) Interior hinged doors and gates: 5 pounds (22.2 N) maximum. (b) Sliding or folding doors: 5 pounds (22.2 N) maximum. These forces do not apply to the force required to retract latch bolts or disengage other devices that hold the door or gate in a closed position.
- **Primary source:** [2010 Standards 404](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#404-doors-doorways-and-gates) and [Access Board 404.2.9](https://www.access-board.gov/ada/#ada-404_2_9)
  > 404.2.9 Door and Gate Opening Force. Fire doors shall have a minimum opening force allowable by the appropriate administrative authority. The force for pushing or pulling open a door or gate other than fire doors shall be as follows:
  > Interior hinged doors and gates: 5 pounds (22.2 N) maximum.
  > Sliding or folding doors: 5 pounds (22.2 N) maximum.
  > These forces do not apply to the force required to retract latch bolts or disengage other devices that hold the door or gate in a closed position.

  [Access Board guide, Chapter 4: Entrances, Doors, and Gates](https://www.access-board.gov/ada/guides/chapter-4-entrances-doors-and-gates/):
  > The opening force of exterior swing doors is impacted by wind loading and other exterior conditions, gasketing, HVAC systems, energy efficiency, and the weight of doors. The minimum force needed to ensure proper closure and positive latch usually exceeds the accessible limit of 5 pounds of force (lbf) required at other doors. For this reason, a maximum opening force is not specified for exterior hinged doors.
- **Match:** Yes, word for word. The pack adds the "(a)" and "(b)" markers.
- **What the check does:** `checks/questions.py:40` attaches the entrance door. The owner reads `agents/copy.py:252-255`: "Push the front door open with one finger and tell us if it gives" and "A door should open with 5 pounds of push". A hinged front door that opens to the street is an exterior hinged door, and 404.2.9 sets no limit for it. `D-to-C.md` says CBC 11B-404.2.9 limits exterior doors. I could not fetch the CBC text, so confirm that before any question cites it. The guide measures opening force with a pressure gauge, and nothing in the Standards mentions a one-finger test.
- **Fix owner:** Lane C. Either ask about an interior door, or cite the California section after someone reads it. Changing the section means verifying this rule again.
- **Verdict:** Verify, fix logic

## Keep for the demo

### `point_of_sale_height`

- **Rule id:** `point_of_sale_height`
- **Citation in pack:** ADA 2010, 904.4
- **Threshold in pack:** 36 in, at most. The parameter is `accessible_length_min_inches` 36.
- **Pack source_text:**
  > 904.4.1 Parallel Approach. A portion of the counter surface that is 36 inches (915 mm) long minimum and 36 inches (915 mm) high maximum above the finish floor shall be provided. Advisory 904.2 Approach. Where a cash register is provided at the sales or service counter, locate the cash register at the accessible section of the counter.
- **Pack review_note:**
  > 904.2's register sentence is an advisory. The check fires only when a lowered section already meets 904.4.1 and the point of sale still sits on a higher section, which is the fact pattern in Whitaker v. T Rock Inc.
- **Primary source:** [2010 Standards, Advisory 904.2](https://www.ada.gov/law-and-regs/design-standards/2010-stds/#advisory-904.2-approach.), [Access Board 904.2](https://www.access-board.gov/ada/#ada-904_2) and [Access Board 227.3](https://www.access-board.gov/ada/#ada-227_3)
  > 904.2 Approach. All portions of counters required to comply with 904 shall be located adjacent to a walking surface complying with 403.
  >
  > Advisory 904.2 Approach. If a cash register is provided at the sales or service counter, locate the accessible counter close to the cash register so that a person using a wheelchair is visible to sales or service personnel and to minimize the reach for a person with a disability.
  >
  > Advisory 227.3 Counters. Types of counters that provide different services in the same facility include, but are not limited to, order, pick-up, express, and returns. One continuous counter can be used to provide different types of service. For example, order and pick-up are different services. It would not be acceptable to provide access only to the part of the counter where orders are taken when orders are picked-up at a different location on the same counter. Both the order and pick-up section of the counter must be accessible.

  904.4 and 904.4.1 are quoted under `service_counter_height`.
- **Match:** No. The pack's advisory sentence does not appear in the 2010 Standards. The pack says to "locate the cash register at the accessible section of the counter". Advisory 904.2 says to "locate the accessible counter close to the cash register". Neither 904.4 nor 904.4.1 mentions a register. The 36 in number itself matches, because 904.4.1 and 904.4.2 both set "36 inches (915 mm) high maximum".
- **What the check does:** `checks/service_counter.py:130-163` runs only when a lowered section stands beside a counter. It finds each card reader or register whose footprint touches a counter section and fails when that section is over 36 in. It reads the portion size from `service_counter_height`'s rule (`service_counter.py:132-135`), so it uses that rule's numbers even when `service_counter_height` has no ledger entry.
- **Decision:** Brendan decided on 2026-09-12 to keep this rule for the demo, so record it tonight like the others. The sample shop keeps "People pay at the high counter". Brendan's decision reached this sheet through the other Lane D session.
- **Still to fix:** Lane C replaces the misquote in `source_text` with the Advisory 904.2 wording above, and ideally adds Advisory 227.3, so the report cites words that appear in the 2010 Standards. The ledger entry binds only id, section, threshold and unit, so that fix leaves it valid as long as the section stays 904.4.
- **Verdict:** Keep for the demo (Brendan, 2026-09-12)

## Hold

### `exit_path`

- **Rule id:** `exit_path`
- **Citation in pack:** CBC 2022, "Chapter 10" ([pack URL](https://codes.iccsafe.org/content/CABC2022P4/chapter-10-means-of-egress))
- **Threshold in pack:** 36 in, at least. It has no parameters.
- **Pack source_text:**
  > DRAFT, section not yet pinned. Chapter 10 governs means of egress. The width of exit access is scoped by 1020.2 and the egress width per occupant by 1005.3, and which one governs a small shop depends on occupant load.
- **Pack review_note:**
  > Pin the exact section and confirm the occupant load that applies to a shop of this size before enabling. An accessibility professional or the Palo Alto Building Division should confirm this one.
- **Primary source:** Not fetched. The ICC page in the pack returned only a subscription notice and no section text. I have no quote for this rule.
- **Match:** Cannot be checked. The pack names a chapter rather than a section, and its own text says it is a draft.
- **What the check does:** `checks/exit_path.py:30-73` checks only that every leg of the confirmed route can be walked, and it treats the last stop as the exit when no stop is named "Exit" (`exit_path.py:47-51`). It never compares a width with 36 in. The owner reads "There's a path from every seat to the way out" (`agents/copy.py:230`), but the sample shop's route has a single Seat stop.
- **To unblock:** Someone with CBC access pins the section and the occupant load, as the review note asks. The note names 1020.2 and 1005.3 as candidates. Lane C then updates `citation.section`, `source_text` and `threshold`, and the rule gets verified against that section.
- **Verdict:** Hold

## Checklist

| Rule id | Citation | Verdict | Reviewer | Second check |
|---|---|---|---|---|
| `entrance_threshold` | ADA 2010 303.3 | Verify | | |
| `door_hardware` | ADA 2010 404.2.7 | Verify | | |
| `floor_surface` | ADA 2010 302 | Verify | | |
| `restroom_turning_space` | ADA 2010 603.2.1 | Verify | | |
| `route_clear_width` | ADA 2010 403.5.1 | Verify, fix logic | | |
| `turn_clear_width` | ADA 2010 403.5.2 | Verify, fix logic | | |
| `passing_space` | ADA 2010 403.5.3 | Verify, fix logic | | |
| `turning_space` | ADA 2010 304.3.1 | Verify, fix logic | | |
| `door_clear_width` | ADA 2010 404.2.3 | Verify, fix logic | | |
| `service_counter_height` | ADA 2010 904.4.1 | Verify, fix logic | | |
| `service_counter_approach` | ADA 2010 305.3 | Verify, fix logic | | |
| `door_opening_force` | ADA 2010 404.2.9 | Verify, fix logic | | |
| `point_of_sale_height` | ADA 2010 904.4 | Keep for the demo (Brendan, 2026-09-12) | | |
| `exit_path` | CBC 2022 Chapter 10 | Hold | | |

## Sources fetched for this sheet

| Source | Used for |
|---|---|
| [DOJ, 2010 ADA Standards for Accessible Design](https://www.ada.gov/law-and-regs/design-standards/2010-stds/) | Every 2010 Standards quote |
| [U.S. Access Board, ADA Accessibility Standards](https://www.access-board.gov/ada/) | The same wording, checked section by section, and the per-section links |
| [Access Board guide, Chapter 3: Clear Floor or Ground Space and Turning Space](https://www.access-board.gov/ada/guides/chapter-3-clear-floor-or-ground-space-and-turning-space/) | Where turning space is required (`turning_space`) |
| [Access Board guide, Chapter 4: Entrances, Doors, and Gates](https://www.access-board.gov/ada/guides/chapter-4-entrances-doors-and-gates/) | Exterior door opening force (`door_opening_force`) |
| [ICC, 2022 CBC Chapter 10](https://codes.iccsafe.org/content/CABC2022P4/chapter-10-means-of-egress) | Fetched, but it returned no section text |
| CBC 11B-404.2.9 | Not fetched. The UpCodes chapter 11B page ended before section 11B-404. |
