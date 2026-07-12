"""Task lease protocol for coordinating local and remote workers.

A :class:`LeaseStore` owns the scheduling state for one pipeline run: which
tasks are complete, failed, or currently leased to a worker. Workers interact
with it through a small protocol — ``claim`` a ready task, ``heartbeat`` to
prove liveness, ``renew`` to extend the lease, then ``complete`` or ``fail`` —
and the store enforces the invariants that make this safe across processes:

* A task is only claimable once all of its dependencies have completed, so a
  worker can never run a task before its inputs exist.
* Each claim mints a fresh, HMAC-signed lease token. A worker must present its
  token for every follow-up call; a tampered or stale token is rejected, and a
  reclaim (after expiry) fences the previous holder out by rotating the id.
* A lease expires after ``lease_seconds`` without a heartbeat, at which point
  the task becomes claimable again — a crashed worker cannot strand a task.

The same store drives a single in-process worker (single-process mode) or many
in-process/remote workers through :func:`run_workers`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from limbo.errors import LimboError

DEFAULT_LEASE_SECONDS = 30.0


class LeaseError(LimboError):
    """Raised when a lease token is invalid, expired, or no longer held."""


@dataclass(frozen=True)
class Lease:
    """A worker's claim on a task. ``token`` authenticates follow-up calls."""

    task_id: str
    worker_id: str
    lease_id: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class _ActiveLease:
    worker_id: str
    lease_id: str
    expires_at: float


def _sign(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _make_token(secret: bytes, data: Mapping[str, str]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _sign(secret, payload)
    return base64.urlsafe_b64encode(payload).decode("ascii") + "." + signature


def _read_token(secret: bytes, token: str) -> Dict[str, str]:
    if not isinstance(token, str) or token.count(".") < 1:
        raise LeaseError("malformed lease token")
    encoded, signature = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise LeaseError("malformed lease token") from exc
    expected = _sign(secret, payload)
    if not hmac.compare_digest(expected, signature):
        raise LeaseError("lease token signature is invalid")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LeaseError("malformed lease token") from exc
    if not isinstance(data, dict):
        raise LeaseError("malformed lease token")
    return data


class LeaseStore:
    """Coordinate task leases for one run, safe for concurrent workers."""

    def __init__(self, dependencies: Mapping[str, Iterable[str]], *, secret,
                 lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        self._deps: Dict[str, Set[str]] = {task: set(needs) for task, needs in dependencies.items()}
        for task, needs in self._deps.items():
            missing = [dep for dep in needs if dep not in self._deps]
            if missing:
                raise LeaseError(f"task {task!r} depends on unknown task(s): {', '.join(sorted(missing))}")
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if not self._secret:
            raise LeaseError("lease store requires a non-empty secret")
        self._lease_seconds = float(lease_seconds)
        if self._lease_seconds <= 0:
            raise LeaseError("lease_seconds must be positive")
        self._clock = clock
        self._lock = threading.Lock()
        self._completed: Set[str] = set()
        self._failed: Set[str] = set()
        self._active: Dict[str, _ActiveLease] = {}

    @classmethod
    def from_pipeline(cls, pipeline, *, secret, **kwargs) -> "LeaseStore":
        """Build a store from a :class:`~limbo.spec.PipelineSpec`'s dependency graph."""

        dependencies = {task.id: list(task.needs) for task in pipeline.tasks}
        return cls(dependencies, secret=secret, **kwargs)

    # -- read-only views -------------------------------------------------

    def claimable(self) -> List[str]:
        """Task ids that are ready to run right now (deps done, not leased)."""

        with self._lock:
            return self._claimable_ids(self._clock())

    def status(self, task_id: str) -> str:
        """Return the lifecycle state of a task."""

        with self._lock:
            now = self._clock()
            if task_id not in self._deps:
                raise LeaseError(f"unknown task {task_id!r}")
            if task_id in self._completed:
                return "completed"
            if task_id in self._failed:
                return "failed"
            if task_id in self._blocked():
                return "blocked"
            active = self._active.get(task_id)
            if active and active.expires_at > now:
                return "leased"
            if all(dep in self._completed for dep in self._deps[task_id]):
                return "ready"
            return "pending"

    def finished(self) -> bool:
        """True when no task can make further progress (all done/failed/blocked)."""

        with self._lock:
            now = self._clock()
            if any(active.expires_at > now for active in self._active.values()):
                return False
            return not self._claimable_ids(now)

    # -- worker protocol -------------------------------------------------

    def claim(self, worker_id: str, task_id: Optional[str] = None) -> Optional[Lease]:
        """Atomically lease a ready task (a specific one, or the next available)."""

        with self._lock:
            now = self._clock()
            candidates = self._claimable_ids(now)
            if task_id is not None:
                if task_id not in candidates:
                    return None
                chosen = task_id
            else:
                if not candidates:
                    return None
                chosen = candidates[0]
            lease_id = uuid.uuid4().hex
            expires_at = now + self._lease_seconds
            self._active[chosen] = _ActiveLease(worker_id, lease_id, expires_at)
            token = _make_token(self._secret, {"task_id": chosen, "worker_id": worker_id, "lease_id": lease_id})
            return Lease(chosen, worker_id, lease_id, token, expires_at)

    def heartbeat(self, token: str) -> Lease:
        """Prove the worker is alive; extends the lease by ``lease_seconds``."""

        return self._extend(token, self._lease_seconds)

    def renew(self, token: str, lease_seconds: Optional[float] = None) -> Lease:
        """Extend the lease, optionally for a custom duration."""

        duration = self._lease_seconds if lease_seconds is None else float(lease_seconds)
        if duration <= 0:
            raise LeaseError("lease_seconds must be positive")
        return self._extend(token, duration)

    def complete(self, token: str) -> None:
        """Mark the leased task completed, unblocking its dependents."""

        with self._lock:
            task_id, _ = self._verify(token, self._clock())
            self._completed.add(task_id)
            del self._active[task_id]

    def fail(self, token: str) -> None:
        """Mark the leased task failed; its dependents become blocked."""

        with self._lock:
            task_id, _ = self._verify(token, self._clock())
            self._failed.add(task_id)
            del self._active[task_id]

    # -- internals -------------------------------------------------------

    def _extend(self, token: str, duration: float) -> Lease:
        with self._lock:
            now = self._clock()
            task_id, active = self._verify(token, now)
            expires_at = now + duration
            self._active[task_id] = replace(active, expires_at=expires_at)
            return Lease(task_id, active.worker_id, active.lease_id, token, expires_at)

    def _verify(self, token: str, now: float) -> Tuple[str, _ActiveLease]:
        data = _read_token(self._secret, token)
        task_id = data.get("task_id")
        worker_id = data.get("worker_id")
        lease_id = data.get("lease_id")
        if not isinstance(task_id, str) or not isinstance(worker_id, str) or not isinstance(lease_id, str):
            raise LeaseError("malformed lease token")
        active = self._active.get(task_id)
        if active is None or active.lease_id != lease_id or active.worker_id != worker_id:
            raise LeaseError(f"lease for task {task_id!r} is no longer held by {worker_id!r}")
        if active.expires_at <= now:
            raise LeaseError(f"lease for task {task_id!r} has expired")
        return task_id, active

    def _blocked(self) -> Set[str]:
        blocked: Set[str] = set()
        changed = True
        while changed:
            changed = False
            for task, needs in self._deps.items():
                if task in self._failed or task in blocked:
                    continue
                if any(dep in self._failed or dep in blocked for dep in needs):
                    blocked.add(task)
                    changed = True
        return blocked

    def _claimable_ids(self, now: float) -> List[str]:
        blocked = self._blocked()
        ready = []
        for task, needs in self._deps.items():
            if task in self._completed or task in self._failed or task in blocked:
                continue
            active = self._active.get(task)
            if active is not None and active.expires_at > now:
                continue
            if all(dep in self._completed for dep in needs):
                ready.append(task)
        return sorted(ready)


def run_workers(store: LeaseStore, execute: Callable[[str], bool], worker_ids: Iterable[str],
                idle_sleep: float = 0.001) -> Dict[str, bool]:
    """Drive in-process workers through the lease protocol until the run finishes.

    ``execute(task_id)`` runs the task and returns True on success. With a single
    worker id this is single-process mode; with several it exercises concurrent
    claiming. Returns each task's success flag.
    """

    results: Dict[str, bool] = {}
    results_lock = threading.Lock()

    def worker(worker_id: str) -> None:
        while True:
            lease = store.claim(worker_id)
            if lease is None:
                if store.finished():
                    return
                time.sleep(idle_sleep)
                continue
            try:
                succeeded = bool(execute(lease.task_id))
            except Exception:  # noqa: BLE001 - a crashing task must fail its lease, not the worker
                succeeded = False
            if succeeded:
                store.complete(lease.token)
            else:
                store.fail(lease.token)
            with results_lock:
                results[lease.task_id] = succeeded

    threads = [threading.Thread(target=worker, args=(worker_id,), daemon=True) for worker_id in worker_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results
