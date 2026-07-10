# Project Limbo

Project Limbo is a local-first pipeline engine for teams that need reproducible automation without committing to a heavyweight orchestrator. It runs declarative DAGs of tasks, fingerprints their inputs, skips work that is already up to date, captures structured logs, and fails fast when a dependency chain is unsafe to continue.

The long-term vision is a production-grade scheduler that can start as a single binary on a laptop, then grow into a distributed task system with remote workers, leases, artifact stores, backpressure, and policy controls. The first implementation slice is intentionally practical: a tested CLI runner that can already coordinate data pipelines, build steps, report generation, and other shell-driven workflows.

## Why This Exists

Most useful automation starts small: a few scripts, a handful of files, and a README with the right command order. The pain starts when those scripts need retries, cache invalidation, observability, parallelism, and safe resumption after failure. Limbo turns those implicit conventions into a graph that can be inspected, tested, and executed deterministically.

## Current Capabilities

- JSON pipeline specs with explicit task dependencies.
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
- `command`: shell command to run.
- `needs`: optional list of dependency task IDs.
- `inputs`: optional file paths or glob patterns used for cache fingerprints.
- `outputs`: optional file paths that must exist for a cached task to be reused.
- `env`: optional environment variables for the task.
- `cwd`: optional task working directory relative to the pipeline file.
- `timeout_seconds`: optional timeout for the command.

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

This repository includes an opt-in GitHub Actions workflow at `.github/workflows/autonomous-codex.yml`. It is scheduled every 45 minutes and can also be started manually. The workflow is designed to:

1. Run the test suite first.
2. Stop immediately if tests fail, preserving the rule that the next run must focus on fixing the pipeline.
3. Ask Codex CLI to inspect open issues and implement the next logical ticket.
4. Run tests again.
5. Commit and push changes back to `main` when there is a verified diff.

The workflow requires repository secrets before it can perform model-backed work:

- `OPENAI_API_KEY`: used by Codex CLI.
- A `GITHUB_TOKEN` is provided automatically by GitHub Actions for repository writes when workflow permissions allow it.

The workflow is intentionally auditable: it does not hide test failures, and each autonomous run produces normal Git history.

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
- Issue 2: Built-in JSONL and CSV data operators.
- Issue 3: Retry policies, failure classification, and resumable runs.
- Issue 4: Remote worker protocol with signed task leases.
- Issue 5: Artifact store abstraction for local disk, S3-compatible storage, and content-addressed blobs.
- Issue 6: Metrics, traces, and run visualization.
- Issue 7: Policy engine for command allowlists, secret redaction, and sandbox profiles.
- Issue 8: Distributed scheduler with heartbeats and backpressure.

## License

MIT
