"""Offline, bounded Binance USD-M 15m candle ingestion; never a live feed."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .config import ObserverConfig
from .dataset import build_dataset_artifact, write_dataset
from .snapshots import MISSING_HISTORICAL, SnapshotError, load_json, timestamp
from .util import decimal, stable_id


INGESTION_VERSION = "historical-ingestion-0.1.0"
INTERVAL_MS = 900_000


class IngestionError(ValueError):
    """Source data cannot support the declared historical candle range."""


def _candles(rows: Any, start: int, end: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise IngestionError("klines must be a nonempty array")
    expected_count = (end - start) // INTERVAL_MS
    if len(rows) != expected_count:
        raise IngestionError("klines do not cover the exact requested range")
    result = []
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError("expected a 12-field Binance kline")
            opened, closed = timestamp(row[0]), timestamp(row[6])
            if opened != start + index * INTERVAL_MS:
                raise ValueError("out-of-order, duplicate, missing or misaligned candle")
            if closed != opened + INTERVAL_MS - 1:
                raise ValueError("incorrect 15m close time")
            if any(isinstance(row[i], bool) or not isinstance(row[i], (str, int))
                   for i in (1, 2, 3, 4, 5, 7, 9, 10, 11)):
                raise ValueError("decimal fields must be exact strings or integers")
            o, h, low, close, volume = (decimal(row[i]) for i in range(1, 6))
            if min(o, h, low, close) <= 0 or volume < 0 or not low <= min(o, close) <= max(o, close) <= h:
                raise ValueError("invalid OHLCV bounds")
            quote, buy_base, buy_quote = (decimal(row[i]) for i in (7, 9, 10))
            if min(quote, buy_base, buy_quote) < 0 or buy_base > volume or buy_quote > quote:
                raise ValueError("invalid auxiliary volumes")
            timestamp(row[8])  # Nonnegative integer trade count (not a clock).
            decimal(row[11])  # Provider's ignored scalar cannot contain nested input.
        except (ValueError, TypeError) as exc:
            raise IngestionError(f"kline {index}: {exc}") from exc
        result.append({"open_time_ms": opened, "close_time_ms": closed,
                       "open": o, "high": h, "low": low, "close": close, "volume": volume})
    return result


def import_klines(
    source_path: str | Path, *, base_asset: str, window_size: int = 150,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate an envelope and freeze rolling prefixes as incomplete snapshots.

    The source envelope asserts identity; positional rows cannot authenticate it.
    All snapshots retain acquisition time, including the earliest prefix.
    """
    if not re.fullmatch(r"[A-Z0-9]+", base_asset):
        raise IngestionError("base_asset must be uppercase alphanumeric")
    if type(window_size) is not int or window_size < 1:
        raise IngestionError("window_size must be a positive integer")
    config = ObserverConfig(base_asset=base_asset)
    raw = Path(source_path).read_bytes()
    try:
        envelope = load_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise IngestionError(f"invalid source JSON: {exc}") from exc
    expected = {
        "schema_version": "binance_kline_source/0.1",
        "venue": "binance", "market_type": "usds_m_futures", "interval": "15m",
        "symbol": config.research_symbol, "base_asset": base_asset, "quote_asset": "USDT",
        "price_unit": "USDT_per_base", "volume_unit": "base_asset",
    }
    keys = expected.keys() | {"start_open_time_ms", "end_open_time_ms", "acquired_at_ms", "klines"}
    if not isinstance(envelope, dict) or envelope.keys() != keys:
        raise IngestionError("source envelope has missing or unsupported fields")
    for key, value in expected.items():
        if envelope[key] != value:
            raise IngestionError(f"source {key}: expected {value!r}")
    try:
        start, end, acquired = (timestamp(envelope[key]) for key in
                                ("start_open_time_ms", "end_open_time_ms", "acquired_at_ms"))
    except SnapshotError as exc:
        raise IngestionError(str(exc)) from exc
    if start >= end or start % INTERVAL_MS or end % INTERVAL_MS:
        raise IngestionError("requested [start,end) must be nonempty and aligned to 15m")
    if acquired < end:
        raise IngestionError("acquisition predates final candle completion")
    candles = _candles(envelope["klines"], start, end)
    snapshots = []
    for index, candle in enumerate(candles):
        cutoff = candle["close_time_ms"] + 1
        window = candles[max(0, index + 1 - window_size):index + 1]
        snapshots.append({
            "schema_version": "normalized_snapshot/0.1",
            "observed_at_ms": acquired, "exchange_time_ms": cutoff,
            "research_symbol": config.research_symbol, "executable_pair": config.executable_pair,
            "quote_currency_contract": {"research_quote": "USDT", "executable_quote": "USD",
                                        "parity_assumed": False},
            "market": dict.fromkeys(("binance_last_price", "kraken_last_price",
                                     "kraken_bid_price", "kraken_ask_price")),
            "candles_15m": window, "open_interest_5m": [], "taker_flow_5m": [],
            "funding_history": [],
            "positioning": {"global": [], "top_accounts": [], "top_positions": []},
            "provenance": {"binance_klines": {
                "raw_id": stable_id("reconstructed_window", {"symbol": config.research_symbol,
                                    "acquired_at_ms": acquired, "candles": window}),
                "source": "binance_usds_m_futures", "endpoint": "binance_klines",
                "symbol": config.research_symbol, "event_time_ms": candle["close_time_ms"],
                "received_time_ms": acquired, "critical": True,
            }},
            "quality": {"critical_missing": sorted(MISSING_HISTORICAL), "collection_errors": []},
            "historical_context": {"kind": "candle_only_reconstruction", "availability": "unverified",
                                   "cutoff_ms": cutoff, "acquired_at_ms": acquired},
        })
    metadata = {
        **{key: value for key, value in envelope.items() if key != "klines"},
        "ingestion_version": INGESTION_VERSION, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_identity": "asserted_not_authenticated", "availability": "unverified",
        "content": "candles_only", "window_size": window_size, "candle_count": len(candles),
        "first_cutoff_ms": start + INTERVAL_MS, "last_cutoff_ms": end,
    }
    return build_dataset_artifact(snapshots, source="offline_binance_15m_klines", source_metadata=metadata)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import bounded offline Binance USD-M 15m candles")
    parser.add_argument("--source", required=True, help="source-envelope JSON file")
    parser.add_argument("--base", required=True, help="expected base asset, e.g. TAO")
    parser.add_argument("--output", required=True, help="new normalized JSONL path")
    parser.add_argument("--manifest", required=True, help="new dataset manifest path")
    args = parser.parse_args(argv)
    try:
        rows, manifest = import_klines(args.source, base_asset=args.base.upper())
        write_dataset(rows, manifest, jsonl_path=args.output, manifest_path=args.manifest)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"ingestion failed: {exc}\n")
    print(json.dumps({"dataset_id": manifest["dataset_id"], "snapshot_count": len(rows),
                      "availability": "unverified", "content": "candles_only",
                      "output": args.output, "manifest": args.manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
