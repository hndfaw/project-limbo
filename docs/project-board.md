# Project Board

GitHub Issues are the source of truth for autonomous work selection. This file
mirrors the intended board columns so the repository has an auditable plan.

**Any agent working this repo must read [`AGENTS.md`](../AGENTS.md) first** — it
is the operating manual (how to pick the next task, the implement→test→merge
workflow, environment rules, and which docs to update).

## Done

- [#1 Bootstrap engine foundation, CLI, cache, and CI](https://github.com/hndfaw/project-limbo/issues/1)
- [#2 Add native JSONL and CSV pipeline stages](https://github.com/hndfaw/project-limbo/issues/2)
- [#3 Add retry policies and resumable runs](https://github.com/hndfaw/project-limbo/issues/3)
- [#4 Design and implement remote worker lease protocol](https://github.com/hndfaw/project-limbo/issues/4)
- [#5 Add content-addressed artifact store abstraction](https://github.com/hndfaw/project-limbo/issues/5)
- [#6 Add metrics, event stream, and timeline reports](https://github.com/hndfaw/project-limbo/issues/6)
- [#7 Add command policy, secret redaction, and sandbox profiles](https://github.com/hndfaw/project-limbo/issues/7)

## Ready

- [#17 Re-enable CI to validate every PR](https://github.com/hndfaw/project-limbo/issues/17)
- [#18 Add ruff + mypy lint and type-check gate](https://github.com/hndfaw/project-limbo/issues/18)
- [#19 Verify pip install and the limbo console entry point](https://github.com/hndfaw/project-limbo/issues/19)
- [#20 Refresh architecture doc for leases, artifacts, observability](https://github.com/hndfaw/project-limbo/issues/20)
- [#21 Add CHANGELOG and align package version](https://github.com/hndfaw/project-limbo/issues/21)
- [#22 End-to-end CLI verification pass](https://github.com/hndfaw/project-limbo/issues/22)

## Needs a dedicated session

- [#8 Build durable distributed scheduler service](https://github.com/hndfaw/project-limbo/issues/8) — its foundations (#3–#7) are now all complete, so it is unblocked, but it is a large epic with real design decisions (persistence model, queue fairness, API surface). It should be taken in a focused session and likely split into sub-tickets, not grabbed by a short autonomous slot.

## Autonomous Selection Rule

See [`AGENTS.md`](../AGENTS.md) for the authoritative loop. In short, each run:

1. Runs the full test suite before editing.
2. If tests fail, fixes the failing suite first and does not start feature work.
3. Otherwise chooses the lowest-numbered open, unblocked issue.
4. Implements a focused change with tests and docs, then merges its own PR.
5. Runs the full suite again before merging (never merges red).

## Maintenance model

Development is done by agents working in-session (see [`AGENTS.md`](../AGENTS.md)),
not by a scheduled cloud workflow. The only GitHub Actions workflow is CI
(`.github/workflows/ci.yml`), which runs the test suite on pushes and PRs — see
issue #17 to re-enable it.
