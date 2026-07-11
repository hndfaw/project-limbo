# CLAUDE.md

This repository is maintained autonomously. **Read [`AGENTS.md`](AGENTS.md) — it
is the operating manual and the source of truth for how to work here.**

@AGENTS.md

Key points (see AGENTS.md for the full detail):

- **Pick the next task** = the lowest-numbered open, unblocked GitHub issue
  (`gh issue list --state open`); respect each issue's Dependencies.
- **Every run:** sync `main`, run `PYTHONPATH=src python3 -m unittest discover -s tests`.
  If red, fixing it is the only job this run. If green, implement the next ticket.
- **Ship each ticket end-to-end:** branch → code (match `src/limbo` style) →
  thorough tests → full suite green → update docs (README, roadmap, project-board,
  `__init__` exports) → open PR → **merge it yourself** (`gh pr merge <n>
  --squash --delete-branch`, `Closes #N` in the body) → close the issue → sync main.
- **Never merge a red or partial change.** You are the test gate.
- **Environment rule:** never pipe JSON/brace-content through the shell (it trips
  a security prompt even under bypass permissions) — create files with the Write
  tool and commit with `git commit -F <file>`.
