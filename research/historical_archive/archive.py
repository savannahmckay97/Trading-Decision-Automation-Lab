"""Load a verified data.binance.vision archive (fetch_archive.py) into replay inputs.

Produces the same three series research/setup_frequency/history.py builds from the live API:
  bars   15m klines: open/close times, OHLC, volume
  oi     5-minute points: {"timestamp_ms", "open_interest"} from metrics.sum_open_interest,
         relabelled to the live API's timestamp convention (+5 min; see below)
  taker  5-minute buckets keyed by bucket open: {"buy_volume", "sell_volume"} from 5m klines
         (taker buy volume, and total volume minus taker buy volume). This is NOT identical to
         the live takerlongshortRatio API, which reports slightly less volume in ~40% of buckets;
         FIDELITY.md measures what that does to decisions.
Rows are parsed as published. Duplicate timestamps with different values are an error, not a
silent choice. Nothing is interpolated or filled.
"""
import csv
import datetime as dt
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path


def _rows(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        (name,) = z.namelist()
        text = z.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not any(ch.isdigit() for ch in rows[0][0]):
        rows = rows[1:]  # header row such as open_time/create_time (older files have none)
    return [r for r in rows if r]


def _ms(value):
    v = int(value)
    return v // 1000 if v > 10**14 else v  # some archive files use microseconds


def _put(store, key, value, what):
    if key in store and store[key] != value:
        raise ValueError(f"conflicting {what} rows at {key}: {store[key]} vs {value}")
    store[key] = value


def _kline_files(raw, interval):
    return sorted((raw / "klines" / interval).rglob("*.zip"))


def load_archive(archive_dir):
    archive_dir = Path(archive_dir)
    manifest = json.loads((archive_dir / "fetch_manifest.json").read_text())
    bad = [f for f in manifest["files"] if f["status"] not in ("OK", "MISSING_NOT_PUBLISHED")]
    if bad:
        raise ValueError(f"archive has unverified files: {bad[:3]}")
    raw = archive_dir / "raw"

    bars = {}
    for path in _kline_files(raw, "15m"):
        for r in _rows(path):
            open_ms = _ms(r[0])
            _put(bars, open_ms, {
                "open_time_ms": open_ms, "close_time_ms": _ms(r[6]), "open": Decimal(r[1]),
                "high": Decimal(r[2]), "low": Decimal(r[3]), "close": Decimal(r[4]), "volume": Decimal(r[5]),
            }, "15m kline")

    taker = {}
    for path in _kline_files(raw, "5m"):
        for r in _rows(path):
            open_ms, volume, buy = _ms(r[0]), Decimal(r[5]), Decimal(r[9])
            _put(taker, open_ms, {"timestamp_ms": open_ms, "buy_volume": buy, "sell_volume": volume - buy},
                 "5m taker")

    oi = {}
    for path in sorted((raw / "metrics").glob("*.zip")):
        for r in _rows(path):
            when = dt.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            # The archive labels each point 5 minutes before the live openInterestHist API does.
            # Relabelling at +5 min reproduced all 8,352 API points of the 2026-09-11..10-10
            # backfill exactly (FIDELITY.md), so replay sees the timestamps the observer sees.
            ts = int(when.timestamp() * 1000) + 300_000
            if r[2] == "":
                continue  # Binance occasionally publishes a metrics row with no open interest
            _put(oi, ts, {"timestamp_ms": ts, "open_interest": Decimal(r[2])}, "metrics")

    return {
        "manifest": manifest,
        "bars": [bars[k] for k in sorted(bars)],
        "oi": [oi[k] for k in sorted(oi)],
        "taker": taker,
    }
