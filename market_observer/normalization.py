"""Translate provider-specific payloads into one explicit normalized snapshot."""

from __future__ import annotations

from typing import Any

from .collectors import CollectionError, RawEvent
from .util import decimal, now_ms


def build_snapshot(
    events: dict[str, RawEvent],
    errors: list[CollectionError],
    research_symbol: str,
    executable_pair: str,
) -> dict[str, Any]:
    observed = max((event.received_time_ms for event in events.values()), default=now_ms())
    exchange_time = _value(events, "binance_time", "serverTime") or observed
    price = _payload(events, "binance_price") or {}
    kraken = _kraken_ticker(_payload(events, "kraken_ticker"))
    critical_missing = sorted(
        error.endpoint for error in errors if error.critical
    ) + sorted(name for name in _critical_names() if name not in events)
    snapshot = {
        "schema_version": "normalized_snapshot/0.1",
        "observed_at_ms": observed,
        "exchange_time_ms": int(exchange_time),
        "research_symbol": research_symbol,
        "executable_pair": executable_pair,
        "quote_currency_contract": {
            "research_quote": "USDT",
            "executable_quote": "USD",
            "parity_assumed": False,
        },
        "market": {
            "binance_last_price": decimal(price["price"]) if price.get("price") is not None else None,
            "kraken_last_price": kraken.get("last"),
            "kraken_bid_price": kraken.get("bid"),
            "kraken_ask_price": kraken.get("ask"),
        },
        "candles_15m": _normalize_klines(_payload(events, "binance_klines")),
        "open_interest_5m": _normalize_oi(_payload(events, "binance_oi_history")),
        "taker_flow_5m": _normalize_taker(_payload(events, "binance_taker")),
        "funding_history": _normalize_funding(_payload(events, "binance_funding")),
        "positioning": {
            "global": _normalize_ratio(_payload(events, "binance_global_ratio")),
            "top_accounts": _normalize_ratio(_payload(events, "binance_top_account_ratio")),
            "top_positions": _normalize_ratio(_payload(events, "binance_top_position_ratio")),
        },
        "provenance": {
            name: {
                "raw_id": event.raw_id,
                "source": event.source,
                "endpoint": event.endpoint,
                "symbol": event.symbol,
                "event_time_ms": event.event_time_ms,
                "received_time_ms": event.received_time_ms,
                "critical": event.critical,
            }
            for name, event in sorted(events.items())
        },
        "quality": {
            "critical_missing": sorted(set(critical_missing)),
            "collection_errors": [error.record() for error in errors],
        },
    }
    return snapshot


def _critical_names() -> set[str]:
    return {
        "binance_time",
        "binance_price",
        "binance_klines",
        "binance_oi_history",
        "binance_taker",
        "kraken_ticker",
    }


def _payload(events: dict[str, RawEvent], name: str) -> Any:
    event = events.get(name)
    return event.payload if event else None


def _value(events: dict[str, RawEvent], name: str, key: str) -> Any:
    payload = _payload(events, name)
    return payload.get(key) if isinstance(payload, dict) else None


def _normalize_klines(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            continue
        rows.append(
            {
                "open_time_ms": int(row[0]),
                "open": decimal(row[1]),
                "high": decimal(row[2]),
                "low": decimal(row[3]),
                "close": decimal(row[4]),
                "volume": decimal(row[5]),
                "close_time_ms": int(row[6]),
            }
        )
    return rows


def _normalize_oi(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        try:
            rows.append(
                {
                    "timestamp_ms": int(row["timestamp"]),
                    "open_interest": decimal(row["sumOpenInterest"]),
                    "open_interest_value": decimal(row.get("sumOpenInterestValue", 0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: item["timestamp_ms"])


def _normalize_taker(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        try:
            rows.append(
                {
                    "timestamp_ms": int(row["timestamp"]),
                    "buy_volume": decimal(row["buyVol"]),
                    "sell_volume": decimal(row["sellVol"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: item["timestamp_ms"])


def _normalize_funding(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        try:
            rows.append(
                {
                    "timestamp_ms": int(row["fundingTime"]),
                    "rate": decimal(row["fundingRate"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: item["timestamp_ms"])


def _normalize_ratio(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        try:
            rows.append(
                {
                    "timestamp_ms": int(row["timestamp"]),
                    "long_share": decimal(row["longAccount"]),
                    "short_share": decimal(row["shortAccount"]),
                    "long_short_ratio": decimal(row["longShortRatio"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda item: item["timestamp_ms"])


def _kraken_ticker(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict) or not payload["result"]:
        return {}
    ticker = next(iter(payload["result"].values()))
    try:
        return {
            "ask": decimal(ticker["a"][0]),
            "bid": decimal(ticker["b"][0]),
            "last": decimal(ticker["c"][0]),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
