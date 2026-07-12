# Changelog

All notable changes to Project Limbo are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Versioning convention:** the version lives in `pyproject.toml` and
`src/limbo/__init__.py` (`__version__`) and the two must always match — a test
(`tests/test_packaging.py`) enforces this. When cutting a release, bump both in
the same change, move the `[Unreleased]` notes into a new dated version section,
and tag it (`git tag vX.Y.Z`) / publish a matching GitHub Release.

## [Unreleased]

_No unreleased changes yet._

## [0.1.0] - 2026-07-12

First tagged release of the local-first DAG pipeline engine.

### Added

- **Engine & CLI** — declarative JSON pipelines with DAG validation (duplicate
  IDs, missing dependencies, cycles), dependency-ordered concurrent execution, a
  content-fingerprint cache that skips up-to-date work, per-task logs, and run
  manifests. CLI: `validate`, `plan`, `run`, `runs`, `resume`, `inspect`,
  `timeline`, and `--version`.
- **Data operators** — native JSONL/CSV stages (`filter`, `project`, `rename`,
  `derive`, `join`, `aggregate`) backed by a sandboxed expression evaluator (no
  raw `eval`).
- **Reliability** — retry policies (fixed/linear/exponential backoff, retryable
  exit codes and timeouts) with attempt history, failure summaries, and
  `limbo resume <run-id>` that re-runs only incomplete work while carrying
  successes forward deterministically.
- **Worker lease protocol** — `claim`/`heartbeat`/`renew`/`complete`/`fail` with
  dependency-gated claiming, HMAC-signed tamper-resistant lease tokens, lease
  expiry with holder fencing, and single- or multi-worker drivers.
- **Artifact store** — content-addressed (SHA-256) blob store with streamed
  hashing, atomic idempotent writes, corruption detection, and digest-based
  cache validation.
- **Observability** — JSONL lifecycle event stream, run metrics, and secret
  redaction of environment metadata and generated messages, plus `limbo inspect`
  and `limbo timeline` reports.
- **Policy & safety** — command allow/deny lists (deny-wins, fail-closed; denied
  commands never execute), environment-inheritance policies, and a
  sandbox-profile model.
- **Quality & tooling** — CI on Python 3.9–3.12 with a ruff + mypy
  lint/type-check gate and a package-install smoke check; installable via
  `pip install .` exposing the `limbo` console command; `AGENTS.md` operating
  manual for autonomous contributors. 166 tests.

[Unreleased]: https://github.com/hndfaw/project-limbo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hndfaw/project-limbo/releases/tag/v0.1.0
