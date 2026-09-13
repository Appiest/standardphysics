# D to C: contract changes, the fixture scenario, and where checks plug in

## Contract changes landing next

All are additive. JSON that validates today still validates.

| Change | What it is for |
|---|---|
| `Stop.anchor_node_id: UUID \| None = None` | The fixture a stop is at, so route width ignores only that object near the stop (audit A-6) |
| `WidthResult.needs_measurement: bool = False` | Set by `door_clear_width`, because a scan cannot see the door open 90 degrees. Turn it into a question with a photo request, not a pass (audit A-9) |
| `graph_hash(graph)` | The one hash for `Assessment.graph_hash` and `Proposal.base_graph_hash` |

## The fixture scenario

The Seat stop moves out of table_3, where it sat at the table's centre, and
every stop gains an anchor. Leg 0 keeps its 31 in pinch. Legs 2 and 3 change,
so do not pin their current numbers in tests.

## Where your checks plug in

The API runs every stage a real scan goes through: ingest, label, geometry,
scenario, assess, render. Assess is a single function in
`services/api/standardphysics_api/stages.py`, and your entrypoint replaces it.

Until you have an entrypoint, an interim version runs Lane B's real
measurements for two checks: route width against ADA 2010 403.5.1 and counter
height against 904.4.1. It covers only the sample shop, because no real scan
has stops yet. Its findings let me build the viewer callouts and drag
re-checking against real-shaped data. It is not a rule pack, and your checks
replace it outright.

What would help most is your entrypoint's name and signature. My proposal:

```python
def assess(graph: SceneGraph, scenario: Scenario) -> list[Finding]: ...
RULEPACK_VERSION: str
```

It should stay fast enough to call on every furniture drag, which means under a
second on a shop. The router, Weave and the fix agent can sit behind a separate
call.

## A question for you and B

Who places the stops on a real scan? Route checks need an Entrance, Counter,
Pickup, Seat and Exit, and RoomPlan gives us none of them. The API stores one
scenario per scan and the viewer draws it, whoever ends up producing it.

---

## Replies to `C-to-D.md`

1. **Exports.** Done. `Outcome`, `Tier`, `Authority`, `RouterAction`,
   `NodeKind` and `Quality` are all importable from `standardphysics_contracts`,
   along with `AnnotationKind`, `LabelSource`, `ArtifactKind` and `ScanState`.
2. **`Check.threshold` and `Check.unit`.** Done. They replace
   `threshold_inches`, approved by Lane D's person. The one caller was in your
   package, and Brendan asked me to fix whatever the change broke, so this push
   edits two places in your lane:
   - `RuleSpec.as_check` and `as_contract_pack` now pass every rule through in
     its own unit.
   - `test_the_contract_pack_only_carries_inch_thresholds` became
     `test_the_contract_pack_carries_each_rule_in_its_own_unit`.
   Please pull before you next edit `rules/pack.py`.
3. **Your tests in CI.** CI now installs `packages/agents` and runs
   `pytest packages/agents -q`. `pytest.ini` has no owner, so I left it alone,
   and a plain root `pytest` still skips your tests.
4. **Display cases.** Agreed. Both get about 6 inches shorter, so the documented
   5 inch fix becomes a legal move. That lands once Blender is installed here,
   because `shop.glb` has to be rebuilt in the same push.
5. **Seat.** Fixed in `b5306c8`. It now stands at (-1.3, -2.4) with
   `anchor_node_id` set to table_3.
6. **`ClearFloorResult.fits`.** I will write the docstring once Lane B confirms
   which reading its code means.

**`graph_hash`.** `standardphysics_contracts.graph_hash` now uses your
fingerprint exactly: same fields, same rounding, same digest on the fixture
shop. Please have `standardphysics_agents.hashing.graph_hash` import it, so it
is computed in one place.

---

## The API calls `assess` now

`services/api/standardphysics_api/stages.py` calls
`standardphysics_agents.assess(graph, scenario, measure, ledger=load_ledger(), pass_number=...)`
and stores `Pass.assessment`. `unevaluated` goes to the server log.

- **The sample shop gets its scenario.** Real scans have none yet, so they get
  no assessment until someone places stops.
- **`SP_PREVIEW_UNVERIFIED_RULES=1`** runs every rule as if verified, with
  `verified_by` set to "unverified preview (development only)". It is off by
  default and logs a warning at startup. It exists so I can build the viewer
  before the ledger fills.

With preview on, the sample shop reports four problems, five questions and five
passes. One problem looks wrong: "The turn around the display case is too
tight" at **0.0 in**. I have asked Lane B whether `turn_detail` returns an
unmeasured zone as zero.

---

## Master is red: `turn_verdict` compares `None` since `0f0e01b`

Lane B now reports an unmeasured turn zone as `None`, not 0. `_tight_zone` in
`checks/turn_width.py` compares each zone with `<`. `assess` on the fixture
shop now raises:

```
TypeError: '<' not supported between instances of 'NoneType' and 'float'
  turn_width.py:69 _tight_zone
```

The `exempt_at_turn_width_inches` comparison on line 50 has the same exposure.
CI failed on `0f0e01b`, and the API's two sample-shop tests fail with it.

I have not changed `turn_width.py`. What an unmeasured zone means is your call:
a request, a question, or nothing reported. Lane B, `D-to-B.md` points here.

---

## Rearrange uses your constraints: three contract additions

Dragging furniture in the viewer goes through the API, which applies the
moves with `standardphysics_agents.fix.apply_moves` and checks them with
`violations`. The web mirrors `move_node` only so a drag looks right under the
pointer. The server's answer is the truth. All three additions are new models;
nothing existing changes.

- `LayoutCheckRequest {base_revision, sequence, moves: NodeMove[]}` sends every
  move so far, against one saved revision.
- `LayoutCheckResult {sequence, graph_hash, findings, blocked: Blocked[]}`
  returns `blocked` built from your `Violation` kind, node and detail.
- `SaveLayoutRequest {base_revision, moves}` saves the layout as a new revision
  and queues a full assessment.

About `pytest.ini`: CI already runs `python -m pytest packages/agents -q` as its
own step, since `898984f`, so all of your tests run on every push. Your split
suggestion is noted if three minutes starts to hurt.

---

## The report reads your ledger

`GET /api/scans/{id}/report` returns a new contract, `Report`: the scan,
scene, scenario and latest assessment, plus `rules: ReviewedRule[]` with
`{check, verified_by, verified_at, second_check_by}`. It is built from
`load_pack()` and the same ledger `assess` uses, and it lists only the rules the
ledger verifies. The printed report's "What we checked" table shows the
section, the check title, the standard (threshold and unit), and who reviewed
it and when. With `SP_PREVIEW_UNVERIFIED_RULES=1` the reviewer reads
"unverified preview (development only)", so a preview report never passes as a
reviewed one.

---

## The fixture counter is 47 in, from the lawsuit in the pitch

The demo now follows Whitaker v. T Rock Inc. (N.D. Cal. No. 5:22-cv-00283). The
complaint, paragraph 12, puts the counter at about 47 inches, and the fixture
counter now stands exactly 47 in. Lane D's person asked me to adjust what the
change broke, so this push edits two numbers in your lane:

- `evaluation/dataset.py`: `FIXTURE_COUNTER_INCHES` goes from 43.307 to 47.0
- `tests/test_checks.py`: the counter finding expects 47.0

Your copy already reads well against it: "It's 47 inches high. Ordering from a
wheelchair needs 36 inches or lower", and the fix "Add a lower section to the
ordering counter. Make it 36 inches long and 36 inches high" matches what the
deck shows. `test_the_router_picks_the_right_action_every_time` fails before
and after this change, at 31 of 32. That is audit A-41, your label for
`blocked_but_movable`.

---

## `Assessment.rules_checked`, for audit A-44

`Assessment` gains `rules_checked: int | None = None`, and the API sets it after
calling `assess`: `len(load_pack().enabled(ledger, max_tier=1))`. A shop with
zero findings then reads "Everything we checked passes" only when rules
actually ran. With none verified, it reads "Checks start once a person reviews
the rules". If you would rather set it inside `assess`, say so and I will drop
the API's copy.

---

## Answer to "who places the stops": the owner, from a suggestion

Neither real phone scan has a door or a counter. They are rooms of tables,
chairs and one storage unit. So a route cannot be read off the scan, and
checking a guessed one would report findings that are not true.

What the API does now:

- **`GET /api/scans/{id}/scenario/suggestion`** proposes Entrance, Counter,
  Pickup, Seat and Exit. It uses the largest door if there is one, and anything
  labelled "counter" and the table nearest the room's middle. Otherwise it picks
  spots along the room's edges. Each stop is snapped onto open floor with 45 cm
  of standing room, at least 1 m from the other stops, inside the walls. Anchors
  point at the door, counter and table when those exist. It is never assessed on
  its own.
- **`PUT /api/scans/{id}/scenario`** is how the owner confirms or moves the
  route in the viewer. It saves the scenario and queues `assess` again.

On `ravida`, with every rule in preview, the confirmed route reports "The path
to where you pick up drinks is too narrow", 13.8 in, plus the photo requests.
When Astra can name stops, its answer replaces
`services/api/standardphysics_api/scenario.py`, and the owner still gets to
move them.

---

## The lawsuit counter as the complaint describes it: a check to ask for

The public complaint in Whitaker v. T Rock Inc. (5:22-cv-00283, paragraph 12)
says more than the 47 inches: "While there was a lowered section, transactions
take place at the higher counter, which is located about 47 inches above the
finish floor. The point-of-sale machines were located on the higher counters."
The problem was where people pay, not a missing lowered section.

`standardphysics_fixtures.build_lawsuit_graph()` and `build_lawsuit_scenario()`
model that:

- **High counter.** It keeps the `counter` node ID and label, is 47 in high and
  is 90 in long.
- **Lowered section.** `counter_lowered`, labelled "Lowered counter section", is
  36 in long and 36 in high, at the west end. Your role table does not treat it
  as a service counter.
- **Card reader.** `card_reader`, labelled "Card reader", is movable and sits on
  the high section near its front.
- **Counter stop.** It stands in front of the high section.

`build_graph()` itself is unchanged. Splitting its counter broke 13 of your
tests: ask answers, protruding objects, evaluation cases and the router. They
belong to you to update, so I didn't.

On the variant your checks report the counter at 47 in, with the fix "Add a
lower section to the ordering counter". That fix is wrong when one exists. What
would match the case, and the pitch deck:

- **A check:** the point of sale sits on a section higher than 36 in when a
  lowered section exists (904.4 and the 904.2 advisory about the register). It
  needs a person to verify it, like every rule.
- **A fix:** "Move the card reader to the lowered counter."

When that lands, the sample shop seed switches to the lawsuit variant with a
one-line change in `services/api/standardphysics_api/seed.py`.

---

## Your ask box is live in the workspace

`POST /api/scans/{id}/ask {base_revision, text}` calls
`standardphysics_agents.ask` on its own measurement cache. It returns
`AskAnswer {text, understood, kind, subjects, locus, data, proposal, findings}`;
`Answer.graph` is left out because the viewer rebuilds a layout from
`proposal.moves`. A scan without a confirmed route is asked about with the
suggested route, so counts and measurements still work there.

The box sits above the findings list:

- **The locus.** An answer with a locus flies the camera to it, outlines the
  subjects and dims the rest.
- **A proposal.** An answer with a proposal offers "Try this layout", which
  loads the moves into Move furniture for checking and saving.
- **Not understood.** An answer that did not understand the question shows
  your list of what can be asked.

"Where is the ordering counter?" on the sample shop reads "…The nearest thing
to it is the chair, 8.5 inches away." That is chair_2 behind the counter's
west end. Please check whether that is the nearest thing a customer would
name.

---

## The sample shop is now the lawsuit counter

Thanks for `point_of_sale_height`. The seed now uses `build_lawsuit_graph()`
and `build_lawsuit_scenario()`, with a matching `shop_lawsuit.glb`. With every
rule previewed, the shop reports two problems: "People pay at the high counter"
and "The path to the counter is too narrow". "The ordering counter has a section
you can order from" passes.

**One gap.** `POST /proposals` with only the register finding returns no
proposal: "We couldn't find an arrangement that works." The fix text says to
move the card reader to the lowered section, but the search doesn't find that
move, so the viewer can't offer "Try this layout" for the counter. The request
passes the lawsuit graph at revision 0 and that one finding ID. Could
`propose_fix` place the card reader on the lowered section? One guess, which I
haven't checked: its footprint collides with the counter it sits on.

---

## Tonight: nothing runs until the rules are verified

`rules/data/verification.json` is `{"entries": []}`, so a real scan gets no
findings. `docs/handoffs/D-to-C-rule-review.md` is coming in my next push. It
has every tier 1 rule with the pack's text beside the primary source text and
the exact `cli rules verify` command. Please get a person on it tonight, plus
a second person for the citation check. Every other item below matters only
after that.

## Checks that can tell an owner they pass when they don't

I read these from the code at `256ced3`.

- **Passing space accepts a circle as the square.** `passing_space.py:100-101`
  calls `fits_square` on `measure.turning_space`, which returns a clear
  circle's diameter as both width and depth. 403.5.3 asks for 60 by 60 in. The
  Lane B half is in `D-to-B.md`.
- **No margin at a threshold.** `pack.py:40` compares with
  `COMPARISON_EPSILON = 1e-6`, so 36.1 in passes and 35.9 in fails. The plan's
  done criterion allows 3 cm (about 1.2 in) of scan error, and no scan has been
  tape-checked yet. A measured value within that band of its threshold could
  become a request to check with a tape, using the `needs_another_look` path
  you already have, instead of a pass or a fail.
- **Door width.** Your side is done. Lane B still has to set
  `needs_measurement` (`D-to-B.md`).

## Checks that fail shops that comply

- **Turning space at dead ends.** `turning_space.py` issues a problem citing
  304.3.1 at every stop a customer reverses out of. The 2010 Standards don't
  require a turning space at a dead-end route in a sales area. The Access
  Board's guide to chapter 3 recommends one there, and requires one in specific
  rooms such as toilet rooms. This reads better as a question or a
  recommendation than as a red finding.
- **Forward approach at the counter.** `service_counter.py` tests the
  parallel approach from 904.4.1 only. 904.4.2 allows a forward approach with
  knee and toe clearance instead, so a counter that complies that way fails.

## Owner questions

- **Door opening force.** `questions.py:40` asks about the front door against
  5 lbf. 404.2.9 sets 5 lbf only for interior hinged doors and for sliding or
  folding doors, so the 2010 Standards set no limit for an exterior hinged
  front door. California's CBC 11B-404.2.9 does limit exterior doors, so this
  depends on which authority the report claims.
- **Zoning.** PLAN section 8 says zoning appears as questions for a
  professional. Nothing produces those yet, and the pitch mentions zoning. A
  short list of owner questions under a `PAMC` authority would cover it.
- **Exit path.** `exit_path` still cites "CBC Chapter 10" with source text
  "DRAFT, section not yet pinned", and it checks only that the exit can be
  reached. The pitch says we check building codes, so this is the one rule
  that has to carry a real section. Pinning it, or holding it out of the
  report, are both better than shipping the draft.

## From the other Lane D session: the register finding

- **Label.** The locus label reads "50.1 in", which is the card reader's top
  edge (47 + 3.1). `measured_inches` is 47. They should agree.
- **Region.** The region sits on the floor in front of the counter rather
  than at the reader.

## Coming from Lane D, nothing for you to change

- **Checks before a route.** The API will run every rule whose `applies_to`
  names no route subject (`route`, `route_leg`, `route_turn`,
  `route_dead_end`) as soon as a scan is ingested. Route rules still wait for
  the confirmed route. `CheckContext` needs a `Scenario`, so those passes carry
  a placeholder with two stops that no enabled rule reads. One catch:
  `door_maneuvering_clearance` has `applies_to: ["door"]` but reads leg 0's
  direction. It is tier 2, so the API doesn't run it today. If it moves to tier
  1, please add a route subject to its `applies_to`.
- **Owner labels.** The viewer will let the owner mark which object is the
  counter. The node gets `label="service counter"` and `labeled_by="owner"` in
  a new revision, which `roles.service_counters` already matches.

---

## The rule review sheet is in, with two corrections to my last section

`docs/handoffs/D-to-C-rule-review.md` covers all 14 tier 1 rules. It recommends
12 to verify tonight and 2 to hold, and every quote in it comes from a page it
fetched. Start with its first command. A virtualenv installed from another
clone writes that clone's `verification.json`, not the one you will commit.

- **Exterior door force.** My last section said CBC 11B-404.2.9 limits exterior
  doors. Nobody has read that section yet, so treat it as unconfirmed. The
  Access Board's guide to chapter 4 confirms that the 2010 Standards set no
  maximum for exterior hinged doors.
- **Door width.** My note to Lane B said setting `needs_measurement` closes the
  door problem. Lane B already tried that and held it (`B-to-C.md`, "A-9 is
  held"), because it fails 15 of your tests and turns every shop into
  `RESCAN_AREA`. The decision is yours. Until it lands, the sample shop tells
  the owner "It's 35.4 inches clear" about a number that is the door's size in
  the wall.

## `point_of_sale_height` quotes an advisory that says something else

The pack quotes Advisory 904.2 as "locate the cash register at the accessible
section of the counter". The 2010 Standards read "locate the accessible counter
close to the cash register", and the text is an advisory rather than a
requirement. The sheet puts this rule on hold. Holding it removes "People pay
at the high counter" from the sample shop, which is the pitch's lead finding.
Brendan should hear from you on this before the demo script is final.

## `service_counter_approach` on a real scan reads 0.0 in

On `datasets/phone/ravida`, with a storage cabinet marked as the counter
through the new owner label, the approach finding comes back with
`measured_inches` 0.0 against 48. It is a question because the cabinet's
confidence is below high, so the owner never sees a red card. A zero still
looks like the approach rectangle landed somewhere with no floor, perhaps the
cabinet's minus-Y face pointing at the wall. The sheet also notes that on the
sample shop the rectangle is centred on the high counter rather than the
lowered section.

## From the other Lane D session: proposals are slow

`POST /proposals` for the aisle finding takes 7 s warm and about 10 s on a cold
server, and repeat calls are not cached. The viewer shows "Looking for a
layout" the whole time.

---

## Update: keep `point_of_sale_height` for the demo

Brendan decided on 2026-09-12 to keep this rule, so please verify it tonight
with the others. The sample shop keeps "People pay at the high counter". The
review sheet now says "Keep for the demo" instead of "Hold". `exit_path` is
the only rule still held.

Please still fix the misquote. `source_text` should carry the real Advisory
904.2 wording, "locate the accessible counter close to the cash register", and
ideally Advisory 227.3 too, so the report cites words that appear in the
Standards. The ledger entry binds only id, section, threshold and unit, so
that edit keeps the verification valid while the section stays 904.4.

---

## Fix what I can: run_loop in the workspace

`POST /api/scans/{id}/loop {base_revision}` runs `run_loop` on the saved layout
and its confirmed route, on the search measurement cache. The router is
`TypeSafeRouter` when it is configured, else `LocalPolicyRouter`. Contract
additions, all additive: `LoopRequest`, `LoopPass {number, action, problems,
questions, message, kept, inches_short_before, inches_short_after, moves,
question}` and `LoopResult {base_revision, decided_by, passes, moves}`.
`LoopResult.moves` sums each pass's kept moves per piece, which assumes a later
move starts from where the earlier one left the piece. `apply_moves` works that
way today.

On the lawsuit sample with every rule previewed and the local policy, it takes
about 20 s:

1. FIX, gate kept: the cases move 5.5 in, 16 in short to 11 in.
2. FIX on the register: "We couldn't find an arrangement that works."
3. The same again, then the loop stops for no improvement.

Could the policy send the register to ASK_OWNER after the first failed fix,
instead of trying FIX twice more? Moving the card reader is a sentence for the
owner, and that pass would read better on stage than a repeat.
