"""Shared, side-effect-free availability checks for normalized observations."""

from __future__ import annotations

from typing import Any


class AsOfError(ValueError):
    """Evidence contradicts, or cannot establish, its observation timestamp."""


REQUIRED_ENDPOINTS = (
    "binance_time", "binance_price", "binance_klines",
    "binance_oi_history", "binance_taker", "kraken_ticker",
)
POSITIONING_ENDPOINTS = {
    "global": "binance_global_ratio",
    "top_accounts": "binance_top_account_ratio",
    "top_positions": "binance_top_position_ratio",
}


def _timestamp(value: Any, field: str) -> int:
    # Do not truncate floats or accept booleans/coerced strings as clock truth.
    if type(value) is not int or value < 0:
        raise AsOfError(f"{field}: timestamp must be a non-negative integer")
    return value


def validate_snapshot_asof(snapshot: dict[str, Any]) -> None:
    """Reject detectable unavailable evidence before features or state updates.

    This checks normalized timestamp consistency, not provider publication
    semantics or authenticity. Open candles may remain in the payload; their
    scheduled close is not an availability timestamp. Feature calculations
    retain responsibility for selecting only completed candles.
    """
    observed = _timestamp(snapshot.get("observed_at_ms"), "observed_at_ms")
    exchange = _timestamp(snapshot.get("exchange_time_ms"), "exchange_time_ms")
    if exchange > observed:
        raise AsOfError("exchange_time_ms: future exchange time")
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise AsOfError("provenance: required endpoint records are missing")
    for endpoint in REQUIRED_ENDPOINTS:
        if endpoint not in provenance:
            raise AsOfError(f"provenance.{endpoint}: required endpoint is missing")
    for endpoint, event in provenance.items():
        field = f"provenance.{endpoint}"
        if not isinstance(event, dict):
            raise AsOfError(f"{field}: endpoint record must be an object")
        receipt = _timestamp(event.get("received_time_ms"), f"{field}.received_time_ms")
        if receipt > observed:
            raise AsOfError(f"{field}.received_time_ms: future receipt")
        event_time = event.get("event_time_ms")
        # Kraken REST freshness uses receipt time; no exchange event time is
        # asserted when that field is unavailable. Optional empty endpoints can
        # also lack an event time; supplied series below still require one.
        if event_time is None and (endpoint == "kraken_ticker" or endpoint not in REQUIRED_ENDPOINTS):
            continue
        event_time = _timestamp(event_time, f"{field}.event_time_ms")
        # The collector's kline envelope records max(close_time), including an
        # open candle. It is a scheduled boundary, not a publication timestamp.
        if endpoint != "binance_klines" and event_time > observed:
            raise AsOfError(f"{field}.event_time_ms: future event")

    def series(name: str, rows: Any, endpoint: str, *, required: bool = False) -> None:
        if not isinstance(rows, list) or (required and not rows):
            raise AsOfError(f"{name}: {'required non-empty' if required else 'expected'} series")
        if rows and endpoint not in provenance:
            raise AsOfError(f"{name}: missing provenance.{endpoint}")
        if rows:
            _timestamp(provenance[endpoint].get("event_time_ms"), f"provenance.{endpoint}.event_time_ms")
        for index, row in enumerate(rows):
            field = f"{name}[{index}].timestamp_ms"
            if not isinstance(row, dict):
                raise AsOfError(f"{name}[{index}]: row must be an object")
            timestamp = _timestamp(row.get("timestamp_ms"), field)
            if timestamp > observed:
                raise AsOfError(f"{field}: future input")

    series("open_interest_5m", snapshot.get("open_interest_5m"), "binance_oi_history", required=True)
    series("taker_flow_5m", snapshot.get("taker_flow_5m"), "binance_taker", required=True)
    series("funding_history", snapshot.get("funding_history", []), "binance_funding")
    positioning = snapshot.get("positioning", {})
    if not isinstance(positioning, dict):
        raise AsOfError("positioning: series must be an object")
    for name, endpoint in POSITIONING_ENDPOINTS.items():
        series(f"positioning.{name}", positioning.get(name, []), endpoint)
