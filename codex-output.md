Implemented issue #2: native JSONL/CSV data operators.

- Added filter, project, inner/left join, and aggregation operators.
- Integrated validation, caching, atomic outputs, logs, and task failure handling.
- Documented operator configuration.
- Added exhaustive unit and integration coverage.
- Preserved existing user work; no commit created.

Verification: all 27 tests pass, compilation and diff checks pass.