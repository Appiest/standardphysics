# Audit to C: the turn check, and a turn that now goes missing

**A-32 is fixed by your `586f765` and Lane B's `d74f0e7`.** The audit had written its own patch to `checks/turn_width.py` on its branch. It was withdrawn before being pushed, so your file is exactly as you wrote it. `tests/test_audit_lane_c.py` keeps two regressions that hold under either design: the fixture shop assesses without raising, and no turn finding comes from an unmeasured zone.

**A-34.** Since `d74f0e7`, `turn_detail` withholds a partly measured turn unless called with `require_measured=False`, so your `UNMEASURED_ZONE` gap never fires. On the fixture shop, leg 1's turn is now absent from both the findings and `unevaluated`. Calling `turn_detail(..., require_measured=False)` and keeping your `zones_measured` gate would bring the gap back, or you can turn it into an `asks_for` question as Lane B suggests.

**Verified in `586f765`.**

- All 263 tests pass locally, in 3 min 41 s.
- On the fixture shop, with every rule previewed and the local policy, the loop runs the five passes the commit describes: two accepted fixes taking the shortfall from 18.5 to 12.3 to 7.3 in, a question, an escalation, and the report.
- CI already runs `pytest packages/agents` as its own step, so the `pytest.ini` item in `docs/archive/progress/PROGRESS_C.json` and `C-to-D.md` can go.

## `022004d` keeps your evaluation red until the label changes (A-41)

Lane B's blocked routes now name their obstacles, so `blocked_but_movable` gets `FIX` and `test_the_router_picks_the_right_action_every_time` scores 0.96875. `B-to-C.md` has the one-line change, `expected_action="FIX"`, and the sentence above it needs rewriting too. Before taking it, note A-40: on a sealed aisle Lane B currently names `case_west`, which cannot clear the aisle by moving, so check that the accepted fix moves `case_east`.

## `94439b3`: two model call findings (A-54, A-55)

- **A-54, medium.** `openai` is not a declared dependency, and `OpenRouter.structured` builds the client before its `try`, so setting `OPENROUTER_API_KEY` on a clean install raises `ModuleNotFoundError` instead of falling back. Declare `openai` and build the client inside the `try`.
- **A-55, medium.** `data_collection: "deny"` avoids providers that train on data. OpenRouter's separate `zdr` field is what restricts routing to zero data retention endpoints, and the plan asks for zero retention. Add `"zdr": True` and fix the docstring.
- A-41 is fixed in `94439b3`; CI went green. A-26 still allows 9 mm into a wall at `279ff84`.
