from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import time
from typing import Any


def utc_now_iso_ms() -> str:
    """Get current UTC time down to the millisecond."""
    now = datetime.now(timezone.utc)
    ms = now.microsecond // 1000
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def epoch_ms_now() -> int:
    """Get current time as whole milliseconds since Jan 1, 1970."""
    return time.time_ns() // 1_000_000


def now_perf_ns() -> int:
    """Read high-speed timer in nanoseconds for accurate speed checks."""
    return time.perf_counter_ns()


def ns_to_ms(duration_ns: int, decimals: int = 3) -> float:
    """Turn nanoseconds into milliseconds."""
    safe_ns = max(0, int(duration_ns))
    return round(safe_ns / 1_000_000.0, decimals)


def ns_to_seconds(duration_ns: int, decimals: int = 4) -> float:
    """Turn nanoseconds into seconds."""
    safe_ns = max(0, int(duration_ns))
    return round(safe_ns / 1_000_000_000.0, decimals)


def ms_to_seconds(duration_ms: float, decimals: int = 4) -> float:
    """Turn milliseconds into seconds."""
    safe_ms = max(0.0, float(duration_ms))
    return round(safe_ms / 1000.0, decimals)


def seconds_to_ms(duration_s: float, decimals: int = 3) -> float:
    """Turn seconds into milliseconds."""
    safe_s = max(0.0, float(duration_s))
    return round(safe_s * 1000.0, decimals)


def format_duration_precise(duration_ms: float) -> str:
    """Show exact milliseconds and seconds in one clean line."""
    safe_ms = max(0.0, float(duration_ms))
    seconds_val = ms_to_seconds(safe_ms, decimals=4)
    return f"{safe_ms:.3f} ms ({seconds_val:.4f} s)"


@dataclass
class TimingRecord:
    """Saved timing result for one finished step or task."""

    label: str
    started_at_utc_ms: str
    completed_at_utc_ms: str
    duration_ns: int
    duration_ms: float
    duration_s: float
    succeeded: bool
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StepTimer:
    """Measure how long one step takes from start to finish."""

    def __init__(self, label: str) -> None:
        clean_label = (label or "").strip()
        self.label = clean_label if clean_label else "step"
        self._start_ns: int = 0
        self._end_ns: int = 0
        self.started_at_utc_ms: str = ""
        self.completed_at_utc_ms: str = ""
        self.succeeded: bool = False
        self.error_message: str | None = None
        self.record: TimingRecord | None = None

    def __enter__(self) -> "StepTimer":
        self.started_at_utc_ms = utc_now_iso_ms()
        self._start_ns = now_perf_ns()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self._end_ns = now_perf_ns()
        self.completed_at_utc_ms = utc_now_iso_ms()
        elapsed_ns = max(0, self._end_ns - self._start_ns)
        elapsed_ms = ns_to_ms(elapsed_ns, decimals=3)
        elapsed_s = ns_to_seconds(elapsed_ns, decimals=4)

        if exc_val is not None:
            self.succeeded = False
            self.error_message = f"{type(exc_val).__name__}: {exc_val}"
        else:
            self.succeeded = True
            self.error_message = None

        self.record = TimingRecord(
            label=self.label,
            started_at_utc_ms=self.started_at_utc_ms,
            completed_at_utc_ms=self.completed_at_utc_ms,
            duration_ns=elapsed_ns,
            duration_ms=elapsed_ms,
            duration_s=elapsed_s,
            succeeded=self.succeeded,
            error_message=self.error_message,
        )
        # Let errors bubble up so nothing hides a real problem.
        return False

    @property
    def elapsed_ms(self) -> float:
        end_point = self._end_ns if self._end_ns > 0 else now_perf_ns()
        if self._start_ns <= 0:
            return 0.0
        return ns_to_ms(max(0, end_point - self._start_ns), decimals=3)

    @property
    def elapsed_s(self) -> float:
        end_point = self._end_ns if self._end_ns > 0 else now_perf_ns()
        if self._start_ns <= 0:
            return 0.0
        return ns_to_seconds(max(0, end_point - self._start_ns), decimals=4)


class BatchStopwatch:
    """Track total run time and every item's time during a batch job."""

    def __init__(self, batch_name: str = "batch_run") -> None:
        cleaned_name = (batch_name or "").strip()
        self.batch_name = cleaned_name if cleaned_name else "batch_run"
        self.started_at_utc_ms: str = utc_now_iso_ms()
        self._batch_start_ns: int = now_perf_ns()
        self.items: list[TimingRecord] = []

    def add_item_timing(
        self,
        label: str,
        duration_ms: float,
        started_at_utc_ms: str,
        completed_at_utc_ms: str,
        succeeded: bool = True,
        error_message: str | None = None,
    ) -> TimingRecord:
        safe_ms = max(0.0, float(duration_ms))
        rec = TimingRecord(
            label=label,
            started_at_utc_ms=started_at_utc_ms,
            completed_at_utc_ms=completed_at_utc_ms,
            duration_ns=int(round(safe_ms * 1_000_000.0)),
            duration_ms=round(safe_ms, 3),
            duration_s=ms_to_seconds(safe_ms, decimals=4),
            succeeded=succeeded,
            error_message=error_message,
        )
        self.items.append(rec)
        return rec

    def total_elapsed_ms(self) -> float:
        return ns_to_ms(max(0, now_perf_ns() - self._batch_start_ns), decimals=3)

    def total_elapsed_s(self) -> float:
        return ns_to_seconds(max(0, now_perf_ns() - self._batch_start_ns), decimals=4)

    def summary(self) -> dict[str, Any]:
        total_ms = self.total_elapsed_ms()
        sum_ms = round(sum(item.duration_ms for item in self.items), 3)
        ok_count = sum(1 for item in self.items if item.succeeded)
        fail_count = len(self.items) - ok_count
        avg_ms = round(sum_ms / len(self.items), 3) if self.items else 0.0
        return {
            "batch_name": self.batch_name,
            "started_at_utc_ms": self.started_at_utc_ms,
            "ended_at_utc_ms": utc_now_iso_ms(),
            "total_elapsed_ms": total_ms,
            "total_elapsed_s": ms_to_seconds(total_ms, decimals=4),
            "sum_of_item_ms": sum_ms,
            "sum_of_item_s": ms_to_seconds(sum_ms, decimals=4),
            "avg_item_ms": avg_ms,
            "avg_item_s": ms_to_seconds(avg_ms, decimals=4),
            "item_count": len(self.items),
            "succeeded_count": ok_count,
            "failed_count": fail_count,
        }
