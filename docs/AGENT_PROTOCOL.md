# How agents work on this repo

Every agent in every lane follows this. It exists so four people and their agents can write code at the same time without stepping on each other.

Read your lane document in `docs/lanes/` for what to build. Read `docs/PLAN.md` for what the product is. Read `CLAUDE.md` for how the interface and the copy must look. Read [`AGENTS.md`](../AGENTS.md) for how your work reaches master: you branch from master and merge back into it, and that file is the one place the workflow is written down.

---

## The loop

Run this continuously. Never go more than about fifteen minutes without pulling master.

```
1. Pull master and branch from it for the task, as AGENTS.md says
2. Pick the next unfinished task from your lane document
3. Build it
4. Run your lane's tests
5. Commit each unit that works on your branch
6. Update docs/progress/PROGRESS_<LANE>.json and commit it on the same branch
7. Pull master again, then merge your branch into master, as AGENTS.md says
8. Go to 1
```

Pull master before you start a task and again before you merge. Somebody else changed something in the last ten minutes and you want to know now, not at the merge.

Commit after every unit that works. A unit is a function with its test, a screen that renders, an endpoint that returns. Do not batch a morning of work into one commit, and merge a finished task back promptly instead of letting the branch run for days. Small commits and short branches merge cleanly; large ones fight.

Never `git push --force`. Never `git rebase` a branch someone else has.

## Staying in your lane

Your lane document lists the paths you own. **Only edit files under those paths.**

Two exceptions, both narrow:

- `packages/contracts/` is owned by Lane D. Everyone reads it, nobody else writes it.
- `docs/handoffs/` is where you ask another lane for something.

When you need a change in another lane's files, do not make it. Write a file at `docs/handoffs/<your-lane>-to-<their-lane>.md` describing what you need and why, commit it, and merge it into master. Then keep working on something else. Check `docs/handoffs/` for requests aimed at you on every pull.

If two lanes genuinely need to edit the same file, that file is in the wrong place. Flag it to the human in your lane instead of working around it.

## When a pull conflicts

In a file your lane owns, resolve it yourself and keep going. Someone probably pulled an older copy.

In a file your lane does not own, stop. Do not resolve it. `git merge --abort`, then tell the human in your lane which file and which lanes are involved.

## Tests before merging

Run your lane's test command before every merge into master. A red test on `master` blocks three other lanes, and the person who finds it is not the person who broke it.

Python lanes: `pytest packages/<yours> -q`
Web: `npm run typecheck && npm run test`
iOS: `xcodebuild test` on the simulator scheme

If a test fails for a reason outside your lane, merge nothing, write the handoff, and move to the next task.

## Progress

Keep `docs/progress/PROGRESS_<LANE>.json` at the repo root current. Other lanes read it to know what they can rely on.

```json
{
  "lane": "B",
  "updated": "2026-09-12T15:40:00Z",
  "done": ["usdz import", "coords conversion + test"],
  "in_progress": "occupancy grid",
  "blocked_on": null,
  "ready_for_others": ["packages/pipeline/coords.py", "SceneGraph builder"],
  "needs_human": []
}
```

`ready_for_others` is the important field. It is how Lane C learns that B's measurement functions exist and can be called for real instead of stubbed.

`needs_human` is how the person in your lane learns they are the bottleneck. Put an entry there the moment you hit something an agent cannot do, and keep working on whatever does not depend on it.

## Commit messages

A one-line summary in the imperative, then a blank line, then what changed and why if it is not obvious. Prefix with your lane.

```
B: derive SceneGraph from room.json with unit conversion

RoomPlan hands back Y-up meters. coords.py converts once on ingest
and the test pins both axis order and sign.
```

## What agents must not decide alone

Stop and ask the human in your lane before:

- Changing anything in `packages/contracts/`
- Adding a dependency that other lanes will inherit
- Changing a rule threshold, a citation, or anything in the rule pack
- Spending money, creating an account, or accepting terms
- Anything that touches a real payment, even in test mode

## Writing anything a person will read

Every user-facing string follows section 2 of `docs/PLAN.md`. Short sentences, ordinary words, inches not meters, no jargon, and never a sentence that describes what the product does not do. If you are writing a label, an error, an empty state, or a button, read that section first.
