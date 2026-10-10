"""Allowlisted normalized-snapshot boundary, including lossless JSON decoding.

Only declared numeric fields are converted. Unknown fields are rejected at every
structured level so research results cannot ride along as evaluator inputs.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .util import decimal


class SnapshotError(ValueError):
    """An input does not obey the normalized snapshot contract."""


def timestamp(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise SnapshotError("expected nonnegative integer milliseconds")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotError("expected nonempty string")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise SnapshotError("expected boolean")
    return value


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise SnapshotError("expected finite decimal")
    return decimal(value)


def _positive(value: Any) -> Decimal:
    result = _number(value)
    if result <= 0:
        raise SnapshotError("expected positive decimal")
    return result


def _nonnegative(value: Any) -> Decimal:
    result = _number(value)
    if result < 0:
        raise SnapshotError("expected nonnegative decimal")
    return result


def _share(value: Any) -> Decimal:
    result = _nonnegative(value)
    if result > 1:
        raise SnapshotError("expected share between zero and one")
    return result


def _nullable_number(value: Any) -> Decimal | None:
    return None if value is None else _positive(value)


def _nullable_time(value: Any) -> int | None:
    return None if value is None else timestamp(value)


CANDLE = {**dict.fromkeys(("open", "high", "low", "close"), _positive), "volume": _nonnegative,
          "open_time_ms": timestamp, "close_time_ms": timestamp}
RATIO = {"timestamp_ms": timestamp, "long_share": _share, "short_share": _share,
         "long_short_ratio": _positive}
PROVENANCE = {**dict.fromkeys(("raw_id", "source", "endpoint", "symbol"), _text),
              "event_time_ms": _nullable_time, "received_time_ms": timestamp, "critical": _bool}
ENDPOINTS = {
    "binance_time", "binance_price", "binance_book", "binance_klines", "binance_oi",
    "binance_oi_history", "binance_taker", "binance_funding", "binance_global_ratio",
    "binance_top_account_ratio", "binance_top_position_ratio", "kraken_ticker",
}
MISSING_HISTORICAL = {
    "binance_time", "binance_price", "binance_oi_history", "binance_taker",
    "kraken_ticker", "historical_availability_unverified",
}
SCHEMA = {
    "schema_version": _text, "observed_at_ms": timestamp, "exchange_time_ms": timestamp,
    "research_symbol": _text, "executable_pair": _text,
    "quote_currency_contract": {"research_quote": _text, "executable_quote": _text,
                                "parity_assumed": _bool},
    "market": dict.fromkeys(("binance_last_price", "kraken_last_price",
                             "kraken_bid_price", "kraken_ask_price"), _nullable_number),
    "candles_15m": [CANDLE],
    "open_interest_5m": [{"timestamp_ms": timestamp, "open_interest": _nonnegative,
                          "open_interest_value": _nonnegative}],
    "taker_flow_5m": [{"timestamp_ms": timestamp, "buy_volume": _nonnegative, "sell_volume": _nonnegative}],
    "funding_history": [{"timestamp_ms": timestamp, "rate": _number}],
    "positioning": dict.fromkeys(("global", "top_accounts", "top_positions"), [RATIO]),
    "provenance": dict.fromkeys(ENDPOINTS, PROVENANCE),
    "quality": {"critical_missing": [_text], "collection_errors": [{
        **dict.fromkeys(("source", "endpoint", "symbol", "detail"), _text),
        "critical": _bool, "received_time_ms": timestamp}]},
    "historical_context": {"kind": _text, "availability": _text,
                           "cutoff_ms": timestamp, "acquired_at_ms": timestamp},
}


def _decode(value: Any, schema: Any, path: str) -> Any:
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise SnapshotError(f"{path}: expected object")
        unknown = value.keys() - schema.keys()
        if unknown:
            raise SnapshotError(f"{path}: unsupported fields {sorted(unknown, key=str)}")
        # The root permits candle-only legacy dataset objects. Endpoint entries
        # are optional; every supplied nested record must otherwise be complete.
        if path not in {"snapshot", "snapshot.provenance"}:
            missing = schema.keys() - value.keys()
            if missing:
                raise SnapshotError(f"{path}: missing fields {sorted(missing)}")
        return {key: _decode(item, schema[key], f"{path}.{key}") for key, item in value.items()}
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise SnapshotError(f"{path}: expected array")
        return [_decode(item, schema[0], f"{path}[{i}]") for i, item in enumerate(value)]
    try:
        return schema(value)
    except ValueError as exc:
        raise SnapshotError(f"{path}: {exc}") from exc


def decode_snapshot(value: Any, *, require_complete: bool = False) -> dict[str, Any]:
    """Copy and validate a snapshot; restore known decimals without touching IDs."""
    result = _decode(value, SCHEMA, "snapshot")
    required = {"schema_version", "observed_at_ms", "exchange_time_ms",
                "research_symbol", "executable_pair", "candles_15m"}
    if require_complete:
        required = SCHEMA.keys() - {"historical_context"}
    missing = required - result.keys()
    if missing:
        raise SnapshotError(f"snapshot: missing fields {sorted(missing)}")
    if result["schema_version"] != "normalized_snapshot/0.1":
        raise SnapshotError("unsupported normalized snapshot version")
    if result["exchange_time_ms"] > result["observed_at_ms"]:
        raise SnapshotError("exchange_time_ms is after observed_at_ms")
    cutoff = result["exchange_time_ms"]
    for candle in result["candles_15m"]:
        if candle["close_time_ms"] != candle["open_time_ms"] + 899_999:
            raise SnapshotError("candle interval does not match 15 minutes")
        if not (candle["low"] <= min(candle["open"], candle["close"])
                <= max(candle["open"], candle["close"]) <= candle["high"]):
            raise SnapshotError("invalid OHLC ordering")
    series = [result.get(name, []) for name in
              ("open_interest_5m", "taker_flow_5m", "funding_history")]
    series.extend(result.get("positioning", {}).values())
    for rows in series:
        if any(row["timestamp_ms"] > cutoff for row in rows):
            raise SnapshotError("future input after exchange cutoff")
    for endpoint, event in result.get("provenance", {}).items():
        kraken = endpoint == "kraken_ticker"
        expected_source = "kraken_spot" if kraken else "binance_usds_m_futures"
        expected_symbol = result["executable_pair"] if kraken else result["research_symbol"]
        if (event["endpoint"] != endpoint or event["source"] != expected_source
                or event["symbol"] != expected_symbol):
            raise SnapshotError(f"provenance.{endpoint}: identity mismatch")
        if event["received_time_ms"] > result["observed_at_ms"]:
            raise SnapshotError(f"provenance.{endpoint}: future receipt")
        # Envelope events describe responses collected concurrently. Kraken's
        # event clock is its local receipt; other tickers may also update after
        # the time endpoint returned. Match the shared live guard: envelopes
        # must be observed, while series rows remain below the exchange cutoff.
        if (endpoint != "binance_klines" and event["event_time_ms"] is not None
                and event["event_time_ms"] > result["observed_at_ms"]):
            raise SnapshotError(f"provenance.{endpoint}: future event")
        if (endpoint == "binance_time" and event["event_time_ms"] is not None
                and event["event_time_ms"] != cutoff):
            raise SnapshotError("provenance.binance_time: event does not match exchange_time_ms")
    context = result.get("historical_context")
    if context is not None:
        if (context["kind"] != "candle_only_reconstruction"
                or context["availability"] != "unverified"
                or context["cutoff_ms"] != result["exchange_time_ms"]
                or context["acquired_at_ms"] != result["observed_at_ms"]):
            raise SnapshotError("invalid historical reconstruction context")
        if not MISSING_HISTORICAL <= set(result.get("quality", {}).get("critical_missing", [])):
            raise SnapshotError("historical reconstruction must retain missing-input flags")
        if (any(value is not None for value in result.get("market", {}).values()) or result.get("open_interest_5m")
                or result.get("taker_flow_5m") or result.get("funding_history")
                or any(result.get("positioning", {}).values())):
            raise SnapshotError("candle-only reconstruction cannot contain invented context")
        candles = result["candles_15m"]
        if not candles or any(row["close_time_ms"] >= context["cutoff_ms"] for row in candles):
            raise SnapshotError("historical window contains an incomplete or future candle")
    return result


def load_snapshot_json(payload: str) -> dict[str, Any]:
    return decode_snapshot(load_json(payload))


def load_json(payload: str) -> Any:
    """Reject duplicate keys and nonfinite JSON numbers; preserve decimal tokens."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SnapshotError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise SnapshotError(f"nonfinite JSON value: {value}")

    return json.loads(payload, parse_float=Decimal, parse_constant=invalid, object_pairs_hook=pairs)
