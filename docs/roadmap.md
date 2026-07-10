# Roadmap

## 1. Engine Foundation

Status: implemented in the bootstrap commit.

Acceptance:

- Load JSON pipeline specs.
- Validate duplicate IDs, missing dependencies, and cycles.
- Execute local DAG tasks in dependency order.
- Run independent tasks concurrently.
- Cache successful tasks by fingerprint.
- Capture logs and run manifests.
- Provide `validate`, `plan`, and `run` CLI commands.
- Cover planner, cache invalidation, execution, failure blocking, timeout, and CLI behavior with tests.

## 2. Built-In Data Operators

Add native JSONL and CSV stages so common data pipelines do not need shell snippets for filtering, projection, joins, and aggregation.

## 3. Retry And Resume Policies

Add structured retry policies with exponential backoff, retryable exit-code classification, and `limbo resume <run-id>`.

## 4. Remote Worker Protocol

Introduce signed task leases, worker heartbeats, and lease expiry so a central scheduler can coordinate multiple workers.

## 5. Artifact Store

Add content-addressed artifacts with local disk and S3-compatible backends.

## 6. Observability

Expose metrics, traces, timeline views, and machine-readable event streams.

## 7. Policy And Sandboxing

Add command allowlists, secret redaction, environment policies, and sandbox profiles.

## 8. Distributed Scheduler

Promote the local scheduler into a durable service with backpressure, fairness, and queue isolation.
