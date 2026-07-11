# Project Limbo

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

## Usage

Run from a checkout:

```bash
PYTHONPATH=src python -m limbo.cli validate limbo.json
PYTHONPATH=src python -m limbo.cli plan limbo.json
PYTHONPATH=src python -m limbo.cli run limbo.json
```

After installation:

```bash
limbo validate limbo.json
limbo plan limbo.json
limbo run limbo.json
```

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
- Issue 3: Retry policies, failure classification, and resumable runs.
- Issue 4: Remote worker protocol with signed task leases.
- Issue 5: Artifact store abstraction for local disk, S3-compatible storage, and content-addressed blobs.
- Issue 6: Metrics, traces, and run visualization.
- Issue 7: Policy engine for command allowlists, secret redaction, and sandbox profiles.
- Issue 8: Distributed scheduler with heartbeats and backpressure.

## License

MIT
