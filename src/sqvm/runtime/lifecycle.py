"""Pure lifecycle, cancellation, and cooperative-budget primitives."""

from __future__ import annotations

from enum import StrEnum
from threading import Event
from time import monotonic


class RunState(StrEnum):
    RESERVED = "reserved"
    PREPARED = "prepared"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS = {
    RunState.RESERVED: frozenset({RunState.PREPARED, RunState.FAILED}),
    RunState.PREPARED: frozenset({RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.RUNNING: frozenset({RunState.FINALIZING, RunState.FAILED, RunState.CANCELLED}),
    RunState.FINALIZING: frozenset({RunState.COMPLETED, RunState.FAILED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def allowed_transitions(state: RunState) -> frozenset[RunState]:
    return _ALLOWED_TRANSITIONS[state]


def transition(current: RunState, target: RunState) -> RunState:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"forbidden run transition: {current} -> {target}")
    return target


class CancellationToken:
    """Cooperative-only cancellation. It never preempts an in-flight backend call."""

    def __init__(self) -> None:
        self._event = Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def raise_if_requested(self) -> None:
        if self.requested:
            raise RuntimeError("cancelled")


class CooperativeBudget:
    """Deadline checks for the parent runtime before and after point calls."""

    def __init__(self, point_budget_seconds: float, run_budget_seconds: float) -> None:
        self.point_budget_seconds = point_budget_seconds
        self.run_budget_seconds = run_budget_seconds
        self._started = monotonic()

    def before_point(self) -> float:
        self._check_run()
        return monotonic()

    def after_point(self, point_started: float) -> None:
        now = monotonic()
        if now - point_started > self.point_budget_seconds:
            raise TimeoutError("point budget exceeded")
        if now - self._started > self.run_budget_seconds:
            raise TimeoutError("run budget exceeded")

    def _check_run(self) -> None:
        if monotonic() - self._started > self.run_budget_seconds:
            raise TimeoutError("run budget exceeded")
