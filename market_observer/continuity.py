"""Shared completed-candle chronology classification."""

from __future__ import annotations

from typing import Any


def sequence_status(previous_close_ms: int | None, current_close_ms: int, interval_ms: int) -> dict[str, Any]:
    """Classify chronological continuity between two completed candles."""

    if previous_close_ms is None:
        return {"status": "FIRST", "missing_bars": 0, "delta_ms": None}
    delta = current_close_ms - previous_close_ms
    if delta < 0:
        return {"status": "OUT_OF_ORDER", "missing_bars": 0, "delta_ms": delta}
    if delta == 0:
        return {"status": "DUPLICATE", "missing_bars": 0, "delta_ms": 0}
    if delta == interval_ms:
        return {"status": "CONTIGUOUS", "missing_bars": 0, "delta_ms": delta}
    if delta > interval_ms and delta % interval_ms == 0:
        return {
            "status": "GAP",
            "missing_bars": delta // interval_ms - 1,
            "delta_ms": delta,
            "previous_close_time_ms": previous_close_ms,
            "current_close_time_ms": current_close_ms,
        }
    return {
        "status": "IRREGULAR",
        "missing_bars": 0,
        "delta_ms": delta,
        "previous_close_time_ms": previous_close_ms,
        "current_close_time_ms": current_close_ms,
    }


