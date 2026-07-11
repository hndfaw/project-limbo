# Project Limbo

> **Working on this repo (human or agent)?** Read [`AGENTS.md`](AGENTS.md) first — it is the operating manual: how to pick the next task, the implement → test → merge workflow, and which docs to keep updated.

Project Limbo is a local-first pipeline engine for teams that need reproducible automation without committing to a heavyweight orchestrator. It runs declarative DAGs of tasks, fingerprints their inputs, skips work that is already up to date, captures structured logs, and fails fast when a dependency chain is unsafe to continue.

The long-term vision is a production-grade scheduler that can start as a single binary on a laptop, then grow into a distributed task system with remote workers, leases, artifact stores, backpressure, and policy controls. The first implementation slice is intentionally practical: a tested CLI runner that can already coordinate data pipelines, build steps, report generation, and other shell-driven workflows.

## Why This Exists

Most useful automation starts small: a few scripts, a handful of files, and a README with the right command order. The pain starts when those scripts need retries, cache invalidation, observability, parallelism, and safe resumption after failure. Limbo turns those implicit conventions into a graph that can be inspected, tested, and executed deterministically.

## Current Capabilities

- JSON pipeline specs with explicit task dependencies.
- Native JSONL and CSV filtering, projection, joins, and aggregation.
- DAG validation for duplicate IDs, missing dependencies, and cycles.
- Topological execution with parallel scheduling for independent tasks.
- Content-addressed fingerprints based on task command, environment, declared inputs, declared outputs, and working directory.
- Cache-aware execution that skips tasks whose fingerprint and outputs still match a previous successful run.
- Structured per-task logs under `.limbo/runs/<run-id>/`.
- Dry-run planning, validation, and execution commands.
- Unit and integration tests using only the Python standard library.

## Pipeline Spec

Create a `limbo.json` file:

```json
{
  "version": 1,
  "tasks": [
    {
      "id": "extract",
      "command": "python scripts/extract.py --out build/raw.jsonl",
      "outputs": ["build/raw.jsonl"]
    },
    {
      "id": "normalize",
      "needs": ["extract"],
      "command": "python scripts/normalize.py build/raw.jsonl build/clean.jsonl",
      "inputs": ["build/raw.jsonl"],
      "outputs": ["build/clean.jsonl"]
    }
  ]
}
```

Each task supports:

- `id`: unique task identifier.
- `command`: shell command to run. Specify this or `operator`, but not both.
- `operator`: built-in data operation that avoids invoking a shell.
- `needs`: optional list of dependency task IDs.
- `inputs`: optional file paths or glob patterns used for cache fingerprints.
- `outputs`: optional file paths that must exist for a cached task to be reused.
- `env`: optional environment variables for the task.
- `cwd`: optional task working directory relative to the pipeline file.
- `timeout_seconds`: optional timeout for the command.

## Built-In Data Operators

Operators use paths relative to the pipeline file and automatically declare those paths as cache inputs and outputs. Both `jsonl` and `csv` formats are supported. For example:

```json
{
  "id": "active-users",
  "operator": {
    "type": "filter",
    "format": "jsonl",
    "input": "data/users.jsonl",
    "output": "build/active.jsonl",
    "where": {"field": "active", "equals": true}
  }
}
```

Available operator configurations are:

- `filter`: `input`, `output`, and exactly one of `where` (an object with `field` and `equals`) or `expr` (a safe expression string; the row is kept when it is truthy).
- `project`: `input`, `output`, and a non-empty `fields` list.
- `rename`: `input`, `output`, and a `rename` map of `old -> new` field names. Two fields may not be renamed to the same name, and a rename that would collide with an existing field is rejected.
- `derive`: `input`, `output`, and a `derived` map of `new_field -> expression`. Each expression is evaluated against the source record; derived fields are appended (or overwrite a same-named field). CSV columns are appended in declaration order.
- `join`: `left`, `right`, `output`, `on`, and optional `how` (`inner` or `left`). Colliding right-hand columns receive a `_right` suffix.
- `aggregate`: `input`, `output`, optional `group_by`, and named `aggregations`. Aggregations support `count`, `sum`, `min`, `max`, and `avg`; all except `count` require a `field`.

CSV values are strings when filtering. Numeric CSV and JSONL values are converted to numbers for aggregation. Outputs are written atomically so a failed operation does not leave a partial artifact.

### Safe expressions

`filter` (via `expr`) and `derive` use a sandboxed expression evaluator built on Python's `ast` — never raw `eval`. Bare names resolve to fields of the current record; referencing an absent field is a task failure. Supported features:

- literals, arithmetic (`+ - * / // % **`), comparisons (`== != < <= > >=`, chained), and membership (`in`, `not in`);
- boolean logic (`and`, `or`, `not`) and conditional expressions (`x if cond else y`);
- helper functions: `lower`, `upper`, `strip`, `len`, `abs`, `round`, `int`, `float`, `str`, `bool`, `min`, `max`, `startswith`, `endswith`, `contains`, and `coalesce`.

Attribute access, subscripting, comprehensions, lambdas, and any unlisted function are rejected at load time. Because CSV values are strings, convert them explicitly, e.g. `float(amount) >= 80`.

```json
{
  "id": "senior-actives",
  "operator": {
    "type": "filter",
    "format": "jsonl",
    "input": "data/users.jsonl",
    "output": "build/seniors.jsonl",
    "expr": "active and age >= 45"
  }
}
```

Runnable end-to-end examples live in [`examples/operators_jsonl.json`](examples/operators_jsonl.json) and [`examples/operators_csv.json`](examples/operators_csv.json), each chaining `filter -> derive -> rename -> aggregate` over the sample data in `examples/data/`:

```bash
cd examples
PYTHONPATH=../src python -m limbo.cli run operators_jsonl.json
PYTHONPATH=../src python -m limbo.cli run operators_csv.json
```

## Reliability: Retries and Resumable Runs

Any task may declare a `retry` policy so transient failures are handled without rerunning the whole pipeline:

```json
{
  "id": "fetch",
  "command": "curl -f https://example.com/data > build/data.json",
  "outputs": ["build/data.json"],
  "retry": {
    "max_attempts": 3,
    "backoff": "exponential",
    "delay_seconds": 0.5,
    "max_delay_seconds": 30,
    "retry_on_exit_codes": [1, 7],
    "retry_on_timeout": true
  }
}
```

- `max_attempts` (default `1`): total attempts including the first; `1` disables retries.
- `backoff` (default `fixed`): `fixed`, `linear`, or `exponential` growth of the delay between attempts.
- `delay_seconds` (default `0`) and optional `max_delay_seconds`: base delay and an upper bound.
- `retry_on_exit_codes` (default: any non-zero): restrict retries to specific exit codes.
- `retry_on_timeout` (default `true`): whether a timeout is retryable.

Every attempt is recorded in the run manifest (`.limbo/runs/<run-id>/manifest.json`) with its status, exit code, and duration. When a run fails, the CLI prints a failure summary explaining the final cause and the attempt history, and lists tasks blocked by the failure.

Runs are resumable. Because successful tasks are cached deterministically, resuming re-executes only failed, blocked, or never-run tasks whose dependencies are now satisfied — succeeded work is carried forward unchanged:

```bash
limbo run limbo.json        # fails partway through
limbo resume <run-id>       # restarts only the incomplete work
```

Retry configuration deliberately does not affect a task's fingerprint, so caches stay deterministic across retries and resumes.

## Observability

Every run writes structured **events** and **metrics** so failures are easy to debug:

- `.limbo/runs/<run-id>/events.jsonl` — a JSONL event stream capturing each task lifecycle transition (`run_started`, `task_queued`, `task_started`, `task_succeeded`/`task_failed`/`task_skipped`, `task_blocked`, `run_finished`).
- The run manifest records **metrics** — task counts, cache hits, failure and blocked counts, and total run/queue time.
- **Secret redaction:** environment metadata is passed through a redactor before it reaches an event, so secret-looking values (API keys, tokens, passwords — by name or value shape) are written as `***redacted***` and never touch disk.

Two commands read this back:

```bash
limbo inspect <run-id>    # manifest summary: per-task status, durations, artifacts, and metrics
limbo timeline <run-id>   # readable, relative-time execution timeline from the event stream
```

Example timeline:

```
timeline for 20260101120000-abcd1234
  +  0.000s  run started (3 task(s))
  +  0.001s  a: queued
  +  0.002s  a: started
  +  0.050s  a: succeeded (exit 0)
  +  0.051s  b: failed (exit 1)
  +  0.051s  c: blocked (dependency failed: b)
  +  0.052s  run finished: failed
```

## Artifact Store

Task outputs can be captured in a content-addressed **artifact store** (`src/limbo/artifacts.py`) so they can be verified, deduplicated, and reused. Every blob is addressed by the SHA-256 digest of its contents, which makes storing the same bytes twice idempotent and makes on-disk corruption detectable by recomputing the hash.

An `Artifact` records the metadata a run keeps for each output — `digest`, `size`, `media_type`, the producing task, and the logical (pipeline-relative) path. The `ArtifactStore` offers content-addressed `put_bytes` / `put_file` / `get_bytes` / `exists` / `verify`, with atomic writes and streamed hashing for large files.

Pass a store to the executor to opt in; runs then record each succeeded task's outputs as artifacts in the manifest, and cache validation upgrades from "outputs still exist" to "outputs still match their recorded digest" — so a silently edited or corrupted output invalidates the cache:

```python
from limbo.artifacts import ArtifactStore
from limbo.engine import LocalExecutor

store = ArtifactStore(".limbo/artifacts")
executor = LocalExecutor(".limbo", artifact_store=store)
executor.run(pipeline)  # outputs are ingested; manifest references their artifacts
```

Without a store the executor behaves exactly as before (outputs are checked for existence only), so this is fully opt-in.

## Worker Lease Protocol

The scheduling state for a run can be coordinated through a lease protocol so the same pipeline runs under a single local worker or many remote ones. A `LeaseStore` (see `src/limbo/leases.py`) owns which tasks are complete, failed, or currently leased, and workers interact with it through five transitions:

- **claim** a ready task — only tasks whose dependencies have all completed are ever offered, so a worker can never start a task before its inputs exist;
- **heartbeat** / **renew** to prove liveness and extend the lease;
- **complete** or **fail** to finish it (a failure blocks every downstream dependent).

Each claim mints a fresh **HMAC-signed lease token**; every follow-up call must present it, and a tampered or forged token is rejected. Leases **expire** after a configurable timeout without a heartbeat, at which point the task becomes claimable again and the previous holder is fenced out (its token stops working) — a crashed worker cannot strand a task.

```python
from limbo.leases import LeaseStore, run_workers

store = LeaseStore.from_pipeline(pipeline, secret="shared-secret")
# Single-process mode: one worker drains the graph in dependency order.
run_workers(store, execute=lambda task_id: run_task(task_id), worker_ids=["local"])
# Or many in-process/remote workers claiming independent tasks concurrently:
run_workers(store, execute=run_task, worker_ids=["w1", "w2", "w3"])
```

## Usage

Run from a checkout:

```bash
PYTHONPATH=src python -m limbo.cli validate limbo.json
PYTHONPATH=src python -m limbo.cli plan limbo.json
PYTHONPATH=src python -m limbo.cli run limbo.json
PYTHONPATH=src python -m limbo.cli runs            # list past runs and their status
PYTHONPATH=src python -m limbo.cli resume <run-id>
```

After installation:

```bash
limbo validate limbo.json
limbo plan limbo.json
limbo run limbo.json
limbo runs
limbo resume <run-id>
limbo inspect <run-id>
limbo timeline <run-id>
```

`limbo runs` lists recent runs (newest first) with per-status task counts, so you can find the `<run-id>` to pass to `limbo resume`.

## Autonomous Development Loop

This repository includes an opt-in GitHub Actions workflow at `.github/workflows/autonomous-claude.yml`. It uses Anthropic's official [`claude-code-action`](https://github.com/anthropics/claude-code-action), is scheduled on a true 45-minute cadence, and can also be started manually. The workflow is designed to:

1. Run the test suite first.
2. Stop immediately if tests fail, preserving the rule that the next run must focus on fixing the pipeline.
3. Ask Claude Code to inspect open issues and implement the next logical ticket (without committing).
4. Run tests again to verify the diff.
5. Commit and push changes back to `main` when there is a verified diff.

The workflow requires **one** of these repository secrets before it can perform model-backed work:

- `CLAUDE_CODE_OAUTH_TOKEN` (recommended): generated from a Claude subscription with `claude setup-token`, avoiding metered API billing.
- `ANTHROPIC_API_KEY`: a key from [console.anthropic.com](https://console.anthropic.com), billed per token.

A `GITHUB_TOKEN` is provided automatically by GitHub Actions for repository writes when workflow permissions allow it.

The loop runs on **Claude Sonnet 5** (`--model claude-sonnet-5`) — near-Opus quality on coding and agentic work at a lower per-token cost, which suits a job that runs every 45 minutes. Switch the `--model` line in the workflow to `claude-opus-4-8` for maximum capability, or `claude-haiku-4-5` for the cheapest runs.

The workflow is intentionally auditable: it fails loudly when no Claude credential is configured, does not hide test failures, keeps commit/push under the workflow's control rather than the model's, and each autonomous run produces normal Git history.

## Development

Run tests locally:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

Run a focused command:

```bash
PYTHONPATH=src python -m limbo.cli plan examples/basic.json
```

## Roadmap

- Issue 1: Engine foundation, cache-aware execution, and CLI.
- Issue 2: Built-in JSONL and CSV data operators (filter, project, rename, derive, join, aggregate) with a safe expression evaluator.
- Issue 3: Retry policies (backoff, retryable exit codes/timeouts), attempt history in manifests, failure summaries, and `limbo resume`.
- Issue 4: Worker lease protocol with signed, expiring task leases and dependency-gated claiming.
- Issue 5: Content-addressed artifact store with digest metadata, manifest references, and digest-based cache validation.
- Issue 6: JSONL lifecycle events, run metrics, secret redaction, and `limbo inspect` / `limbo timeline`.
- Issue 7: Policy engine for command allowlists, secret redaction, and sandbox profiles.
- Issue 8: Distributed scheduler with heartbeats and backpressure.

## License

MIT
