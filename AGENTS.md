# AGENTS.md — Operating Manual for Autonomous Agents

**Read this file first.** It is the source of truth for how to work in this
repository. It applies to any agent (Claude Code, Codex, or otherwise). If you
were handed only the GitHub link and told "work on this," follow this file
exactly — do not ask for clarification unless you hit a genuine product decision
you cannot reasonably default (see [When to stop](#when-to-stop)).

Project Limbo is a local-first DAG pipeline engine (a tested CLI runner growing
into a distributed task scheduler). Product/architecture details live in
[`README.md`](README.md) and [`docs/architecture.md`](docs/architecture.md).

---

## Prerequisites (assumed environment)

- **Python 3.9+** on `PATH` as `python3`. The runtime and tests are **stdlib-only** — there is nothing to `pip install` to run the suite.
- **`git`** configured, and **`gh`** (GitHub CLI) authenticated with `repo` + `workflow` scope on this repository, with permission to push and merge. Selecting the next task and merging PRs depend on `gh`.
- **Zero-prompt operation:** if you are a Claude Code session, launch with `--dangerously-skip-permissions` (or ensure `.claude/settings.local.json` sets `permissions.defaultMode` to `bypassPermissions`) so you can work unattended. This does **not** silence the shell obfuscation check — see Environment rules below.

## The loop (do this every run, in order)

1. **Sync:** `git checkout main && git pull --ff-only`.
2. **Run the full test suite:**
   `PYTHONPATH=src python3 -m unittest discover -s tests`
   - **If it is RED, your only job this run is to make it green.** Fix the
     failure, verify, ship the fix, and stop. Do not start new feature work on a
     red suite.
3. **If green, pick the next task:** the **lowest-numbered OPEN, UNBLOCKED**
   GitHub issue. List them with `gh issue list --state open`. An issue is
   blocked if its **Dependencies** section names an issue that is still open.
   Priority order and status mirror [`docs/project-board.md`](docs/project-board.md).
4. **Implement it end-to-end** (see next section).
5. **Repeat** until there are no open, unblocked issues and the suite is green.

---

## How to implement a ticket (end-to-end, every time)

- **Branch:** `git checkout -b <type>/<short-slug>` (e.g. `feat/policy-engine`).
- **Read the issue fully:** `gh issue view <n>`. Honor its Acceptance Criteria
  and Test Plan literally.
- **Write code idiomatically — match the existing style in `src/limbo`:**
  - `from __future__ import annotations`; full type hints; small focused modules.
  - Frozen `@dataclass` for value objects; explicit validation with clear messages.
  - Domain errors subclass `limbo.errors.LimboError`.
  - No new third-party runtime dependencies without a strong reason (stdlib-first).
- **Write thorough tests** in `tests/` using `unittest`, mirroring existing test
  files. Cover the happy path **and** the edge/failure cases named in the issue's
  Test Plan.
- **Run the full suite until green.** You are the test gate. **Never merge broken
  or partial work.**
- **Update docs in the SAME change:**
  - `README.md` — add/adjust the feature + usage section, and the roadmap line.
  - `docs/roadmap.md` — mark the issue's line as delivered.
  - `docs/project-board.md` — move the ticket to **Done**; promote any now-unblocked
    dependents into **Ready**.
  - `src/limbo/__init__.py` — re-export any new public API and keep `__all__` current.
  - Keep `__version__` in `src/limbo/__init__.py` in sync with `pyproject.toml`.
- **Ship it:** commit, push, open a PR, then **merge it yourself**:
  `gh pr merge <n> --squash --delete-branch`. **Never wait for human approval.**
- **Close the issue** with a one-paragraph summary:
  `gh issue close <n> --comment "..."`.
- **Sync main**, then post a one-paragraph summary of what shipped.

---

## Environment rules (CRITICAL — read before running any shell command)

- **Never pipe JSON or brace-content through the shell.** Heredocs like
  `cat > f.json <<'EOF' {...}` and `python - <<'PY' ... {dict} ... PY` contain the
  `{"` pattern, which trips a command-safety prompt ("expansion obfuscation")
  that halts unattended work — even when permissions are set to bypass. Instead:
  - create **every** file (JSON fixtures, scripts, configs) with your file-writing tool,
  - run scratch checks from a written script file, not inline,
  - commit with a written message file and `git commit -F <file>`,
  - keep shell commands free of `{...}` / `${...}` next to quotes.
- **Merging:** you are authorized to merge your own PRs (squash, delete branch)
  and, where configured, push to `main`. Do not wait for approval.
- **Tests are the gate.** You must be green locally before merging. CI
  (`.github/workflows/ci.yml`) also runs the suite on every PR across Python
  3.9–3.12; confirm it is green (`gh pr checks <n>`) before merging. `main` is
  intentionally **not** branch-protected so autonomous self-merge works — that
  makes *you* the gate: never merge a PR whose CI is failing.

---

## Repository layout

| Path | Responsibility |
|---|---|
| `src/limbo/spec.py` | Pipeline JSON parsing + validation (uses `retry.py` for retry policies) |
| `src/limbo/graph.py` | Topological planning; downstream/blocked computation |
| `src/limbo/engine.py` | `LocalExecutor`: scheduling, retries, cache, artifacts, events |
| `src/limbo/operators.py`, `expressions.py` | Built-in data operators + safe expression evaluator (no raw `eval`) |
| `src/limbo/cache.py`, `fingerprint.py` | Content-fingerprint cache |
| `src/limbo/leases.py` | Worker lease protocol (claim/heartbeat/renew/complete/fail) |
| `src/limbo/artifacts.py` | Content-addressed artifact store |
| `src/limbo/observability.py` | Lifecycle events, run metrics, secret redaction |
| `src/limbo/cli.py` | CLI: `validate`/`plan`/`run`/`runs`/`resume`/`inspect`/`timeline` |
| `tests/` | `unittest` suite (run with `PYTHONPATH=src`) |

---

## Commands

```bash
# Full test suite (the gate)
PYTHONPATH=src python3 -m unittest discover -s tests

# Lint + type-check (also enforced by CI; install with: pip install -e ".[dev]")
ruff check src tests
mypy

# Run / inspect a pipeline
PYTHONPATH=src python3 -m limbo.cli run <spec.json>
PYTHONPATH=src python3 -m limbo.cli runs
PYTHONPATH=src python3 -m limbo.cli inspect <run-id>
PYTHONPATH=src python3 -m limbo.cli timeline <run-id>
PYTHONPATH=src python3 -m limbo.cli resume <run-id>
```

---

## Commit & PR conventions

- Small, focused commits; imperative subject line + a short why.
- **Put `Closes #N` in the PR body** (not only the title) — GitHub only
  auto-closes issues from closing keywords in the body/commits.
- Squash-merge and delete the branch.

---

## When to stop

Only stop for a **genuine product decision you cannot reasonably default**, or a
test you cannot make green. In that case: do **not** merge; comment on the issue
(or open one) describing what you tried and the options, and stop. For everything
else, pick a sensible default, note it in the PR, and keep going. **Never merge a
red suite.**

---

## Definition of done (whole project)

All roadmap issues (#1–#8) **and** the quality tickets closed; full suite green;
CI green; README, `docs/architecture.md`, and `CHANGELOG.md` current; the package
installs and the `limbo` entry point works. When that state is reached, report it
and stop.
