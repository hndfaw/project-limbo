"""Domain exceptions raised by Limbo."""


class LimboError(Exception):
    """Base class for user-facing Limbo errors."""


class SpecError(LimboError):
    """Raised when a pipeline specification is invalid."""


class ExecutionError(LimboError):
    """Raised when one or more tasks fail during execution."""
