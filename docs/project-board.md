# Project Board

GitHub Issues are the source of truth for autonomous work selection. The connector available in this Codex session can create and update issues, but does not expose GitHub Projects v2 board creation; the local `gh` token is also invalid. This file mirrors the intended board columns so the repository still has an auditable plan.

## Done

- [#1 Bootstrap engine foundation, CLI, cache, and CI](https://github.com/hndfaw/project-limbo/issues/1)

## Ready

- [#2 Add native JSONL and CSV pipeline stages](https://github.com/hndfaw/project-limbo/issues/2)
- [#3 Add retry policies and resumable runs](https://github.com/hndfaw/project-limbo/issues/3)
- [#5 Add content-addressed artifact store abstraction](https://github.com/hndfaw/project-limbo/issues/5)
- [#6 Add metrics, event stream, and timeline reports](https://github.com/hndfaw/project-limbo/issues/6)
- [#7 Add command policy, secret redaction, and sandbox profiles](https://github.com/hndfaw/project-limbo/issues/7)

## Blocked

- [#4 Design and implement remote worker lease protocol](https://github.com/hndfaw/project-limbo/issues/4), blocked by reliability work.
- [#8 Build durable distributed scheduler service](https://github.com/hndfaw/project-limbo/issues/8), blocked by worker, artifact, observability, and policy foundations.

## Autonomous Selection Rule

Each scheduled run should:

1. Run the full test suite before editing.
2. If tests fail, fix the failing pipeline first and do not start feature work.
3. Otherwise choose the lowest-numbered ready issue whose dependencies are complete.
4. Implement a focused change with tests and docs.
5. Run the full suite again before committing.

## Required Secret

Autonomous runs require the repository secret `OPENAI_API_KEY`. The workflow fails immediately when the secret is missing so missing credentials cannot masquerade as a successful no-op run.
