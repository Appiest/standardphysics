Commit to git whenever a feature is finished and verified (typecheck + tests pass), without waiting to be asked. One commit per feature, message describing the change. Use the default git user. Branch first if on the default branch.

This file is the authority on how work reaches master. [`docs/AGENT_PROTOCOL.md`](docs/AGENT_PROTOCOL.md) and [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) link here instead of repeating it.

master is the only shared branch. Pull master before you start, branch from it for your own work, and merge that branch back into master when it's done. Don't build on someone else's branch, and don't start a new long-lived integration branch; if two branches need combining, merge each into master instead.
