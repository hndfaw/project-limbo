"""Domain exceptions raised by Limbo."""


class LimboError(Exception):
    """Base class for user-facing Limbo errors."""


class SpecError(LimboError):
    """Raised when a pipeline specification is invalid."""


class ExecutionError(LimboError):
    """Raised when one or more tasks fail during execution.

    Carries the :class:`~limbo.engine.RunResult` (when available) so callers can
    render a failure summary with per-task attempt history.
    """

    def __init__(self, message: str, run_result: object = None) -> None:
        super().__init__(message)
        self.run_result = run_result
