"""Retry policies for task execution.

A :class:`RetryPolicy` describes how many times a task may run, how long to
wait between attempts, and which failures are eligible for a retry. The default
policy (``max_attempts=1``) preserves the original single-attempt behavior, so
pipelines that do not opt in are unaffected.

Retry configuration deliberately does not participate in a task's fingerprint:
retries change *how* a task is executed, not the content it produces, so the
cache stays deterministic across retries and resumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

from limbo.errors import SpecError

BACKOFF_STRATEGIES = ("fixed", "linear", "exponential")


@dataclass(frozen=True)
class RetryPolicy:
    """How a task retries on failure."""

    max_attempts: int = 1
    backoff: str = "fixed"
    delay_seconds: float = 0.0
    max_delay_seconds: Optional[float] = None
    retry_on_exit_codes: Tuple[int, ...] = field(default_factory=tuple)
    retry_on_timeout: bool = True

    def delay_for(self, attempt: int) -> float:
        """Return the delay (seconds) to wait after ``attempt`` fails.

        ``attempt`` is 1-based: ``delay_for(1)`` is the pause before the second
        attempt. The result is clamped to ``max_delay_seconds`` when set.
        """

        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.delay_seconds <= 0:
            return 0.0
        if self.backoff == "linear":
            delay = self.delay_seconds * attempt
        elif self.backoff == "exponential":
            delay = self.delay_seconds * (2 ** (attempt - 1))
        else:  # "fixed"
            delay = self.delay_seconds
        if self.max_delay_seconds is not None:
            delay = min(delay, self.max_delay_seconds)
        return float(delay)

    def is_retryable(self, returncode: Optional[int], timed_out: bool) -> bool:
        """Return True when a failure with this outcome is eligible for retry."""

        if timed_out:
            return self.retry_on_timeout
        if returncode is None or returncode == 0:
            return False
        if self.retry_on_exit_codes:
            return returncode in self.retry_on_exit_codes
        return True


NO_RETRY = RetryPolicy()


def parse_retry_policy(value: Any, task_id: str) -> RetryPolicy:
    """Validate and build a :class:`RetryPolicy` from spec configuration."""

    if value is None:
        return NO_RETRY
    if not isinstance(value, Mapping):
        raise SpecError(f"task {task_id!r}: retry must be an object")

    allowed = {"max_attempts", "backoff", "delay_seconds", "max_delay_seconds",
               "retry_on_exit_codes", "retry_on_timeout"}
    unknown = set(value) - allowed
    if unknown:
        raise SpecError(f"task {task_id!r}: unknown retry field(s): {', '.join(sorted(unknown))}")

    max_attempts = value.get("max_attempts", 1)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise SpecError(f"task {task_id!r}: retry max_attempts must be an integer >= 1")

    backoff = value.get("backoff", "fixed")
    if backoff not in BACKOFF_STRATEGIES:
        raise SpecError(f"task {task_id!r}: retry backoff must be one of {', '.join(BACKOFF_STRATEGIES)}")

    delay_seconds = value.get("delay_seconds", 0.0)
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)) or delay_seconds < 0:
        raise SpecError(f"task {task_id!r}: retry delay_seconds must be a non-negative number")

    max_delay_seconds = value.get("max_delay_seconds")
    if max_delay_seconds is not None:
        if isinstance(max_delay_seconds, bool) or not isinstance(max_delay_seconds, (int, float)) or max_delay_seconds <= 0:
            raise SpecError(f"task {task_id!r}: retry max_delay_seconds must be a positive number")

    exit_codes = value.get("retry_on_exit_codes", [])
    if not isinstance(exit_codes, list) or any(
        isinstance(code, bool) or not isinstance(code, int) for code in exit_codes
    ):
        raise SpecError(f"task {task_id!r}: retry retry_on_exit_codes must be a list of integers")

    retry_on_timeout = value.get("retry_on_timeout", True)
    if not isinstance(retry_on_timeout, bool):
        raise SpecError(f"task {task_id!r}: retry retry_on_timeout must be a boolean")

    return RetryPolicy(
        max_attempts=max_attempts,
        backoff=backoff,
        delay_seconds=float(delay_seconds),
        max_delay_seconds=float(max_delay_seconds) if max_delay_seconds is not None else None,
        retry_on_exit_codes=tuple(exit_codes),
        retry_on_timeout=retry_on_timeout,
    )
