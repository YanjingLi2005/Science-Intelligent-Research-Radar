"""Distributed tracing and lightweight context management for Research Radar."""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")


def generate_trace_id() -> str:
    """Generate a compact, timestamp-prefixed trace identifier."""
    return f"tr-{uuid.uuid4().hex[:12]}"


def get_trace_id() -> str:
    """Return the active trace ID or generate a fallback."""
    tid = _current_trace_id.get()
    return tid if tid else "tr-system"


def set_trace_id(trace_id: str | None = None) -> str:
    """Bind a trace ID to the current async context."""
    tid = trace_id.strip() if trace_id and trace_id.strip() else generate_trace_id()
    _current_trace_id.set(tid)
    return tid


class Timer:
    """Lightweight performance timer for execution spans."""

    def __init__(self):
        self.start_time = time.perf_counter()
        self.elapsed_ms = 0

    def stop(self) -> int:
        self.elapsed_ms = int((time.perf_counter() - self.start_time) * 1000)
        return self.elapsed_ms
