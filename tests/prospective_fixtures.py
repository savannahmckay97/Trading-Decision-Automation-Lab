"""Synthetic recorder databases for the prospective v1 data tooling tests.

Self-contained (only ``market_observer`` imports that exist at both current main
and the pinned 76dbb5d), so the same builder runs in-process and inside a pinned
checkout. Each step is one ObserverService cycle over fixture public responses:

* ``["ok", n, offset]``: ``n`` completed 15m candles (candle 100 is a breakout,
  101 a retest, later ones quiet), received ``offset`` ms late;
* ``["missing", n, offset, endpoint]``: the same with one endpoint failing;
* ``["nosnapshot", n, offset]``: collection completion before a receipt, so the
  snapshot can't be built and the live failure decision has no ``snapshot_id``.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from unittest import mock

from market_observer import service as service_module
from market_observer.collectors import CollectionError, RawEvent
from market_observer.config import ObserverConfig
from market_observer.service import ObserverService
from market_observer.storage import EventStore

BASE_TIME = 1_999_999_800_000
INTERVAL = 900_000
DECISION_DELAY_MS = 90_000
CRITICAL = {"binance_time", "binance_price", "binance_klines", "binance_oi_history", "binance_taker", "kraken_ticker"}
SERIES = {"binance_oi_history", "binance_taker", "binance_global_ratio", "binance_top_account_ratio",
          "binance_top_position_ratio"}


def candle_close(n_complete: int) -> int:
    """Close time of the last completed candle when ``n_complete`` candles are complete."""
    return BASE_TIME + n_complete * INTERVAL - 1


def _values(i: int) -> tuple:
    if i == 99:
        return (100, 104, 99, 103, 100)        # breakout
    if i == 100:
        return (103, 103.5, 100.8, 101.5, 20)  # retest
    if i > 100:
        return (101, 102, 100, 101, 10)       # quiet, below the old high
    return (100, 101, 99, 100, 10)


def _kline(open_time, open_, high, low, close, volume):
    return [open_time, str(open_), str(high), str(low), str(close), str(volume), open_time + INTERVAL - 1,
            "0", 1, "0", "0", "0"]


def payloads(n_complete: int) -> tuple[dict, int]:
    rows = [_kline(BASE_TIME + i * INTERVAL, *_values(i)) for i in range(n_complete)]
    last_close = Decimal(str(rows[-1][4]))
    exchange_time = BASE_TIME + n_complete * INTERVAL + 194_000
    rows.append(_kline(BASE_TIME + n_complete * INTERVAL, last_close, last_close + 1, last_close - 1, last_close, 5))
    boundary = BASE_TIME + n_complete * INTERVAL
    times = [boundary - (29 - i) * 300_000 for i in range(30)]
    oi = [{"symbol": "TAOUSDT", "sumOpenInterest": str(1000 + i * 2), "sumOpenInterestValue": str(100_000 + i),
           "timestamp": ts} for i, ts in enumerate(times)]
    taker = [{"buySellRatio": "2.333", "buyVol": "70", "sellVol": "30", "timestamp": ts} for ts in times[:-1]]
    funding = [{"symbol": "TAOUSDT", "fundingTime": exchange_time - (99 - i) * 28_800_000,
                "fundingRate": str(Decimal(i - 50) / Decimal("100000"))} for i in range(100)]
    ratio = [
        {"symbol": "TAOUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5",
         "timestamp": times[-2]},
        {"symbol": "TAOUSDT", "longAccount": "0.62", "shortAccount": "0.38", "longShortRatio": "1.6316",
         "timestamp": times[-1]},
    ]
    bid, ask = last_close - Decimal("0.05"), last_close + Decimal("0.05")
    return {
        "binance_time": {"serverTime": exchange_time},
        "binance_price": {"symbol": "TAOUSDT", "price": str(last_close), "time": exchange_time},
        "binance_book": {"symbol": "TAOUSDT", "bidPrice": str(last_close - Decimal("0.01")),
                         "askPrice": str(last_close + Decimal("0.01")), "time": exchange_time},
        "binance_klines": rows,
        "binance_oi": {"symbol": "TAOUSDT", "openInterest": oi[-1]["sumOpenInterest"], "time": exchange_time},
        "binance_oi_history": oi,
        "binance_taker": taker,
        "binance_funding": funding,
        "binance_global_ratio": ratio,
        "binance_top_account_ratio": ratio,
        "binance_top_position_ratio": ratio,
        "kraken_ticker": {"error": [], "result": {"TAOUSD": {"a": [str(ask), "1", "1"], "b": [str(bid), "1", "1"],
                                                            "c": [str(last_close), "1"]}}},
    }, exchange_time


def events(n_complete: int, offset: int = 0, missing: str | None = None):
    items, exchange_time = payloads(n_complete)
    if missing:
        items.pop(missing)
    result = {}
    for name, payload in items.items():
        if name in SERIES:
            event_time = int(payload[-1]["timestamp"])
        elif name == "binance_funding":
            event_time = int(payload[-1]["fundingTime"])
        else:
            event_time = exchange_time
        source = "kraken_spot" if name == "kraken_ticker" else "binance_usds_m_futures"
        symbol = "TAOUSD" if name == "kraken_ticker" else "TAOUSDT"
        result[name] = RawEvent(source, name, symbol, event_time, exchange_time + offset, payload, name in CRITICAL)
    errors = []
    if missing:
        errors.append(CollectionError("fixture", missing, "TAOUSDT", missing in CRITICAL, exchange_time + offset,
                                      "fixture missing"))
    return result, errors


class StepCollectors:
    def __init__(self, steps):
        self.steps = [list(step) for step in steps]
        self.completed_at_ms = None

    def collect(self):
        step = self.steps.pop(0)
        kind, n_complete, offset = step[:3]
        found, errors = events(n_complete, offset, step[3] if kind == "missing" else None)
        receipts = [event.received_time_ms for event in found.values()] + [e.received_time_ms for e in errors]
        self.completed_at_ms = max(receipts) - (1 if kind == "nosnapshot" else 0)
        return found, errors


def build_db(path, steps, decision_delay_ms: int = DECISION_DELAY_MS):
    """Run one live cycle per step into a new EventStore; decisions are emitted
    ``decision_delay_ms`` after collection completes. Returns the live decisions."""
    collectors = StepCollectors(steps)
    decisions = []
    with EventStore(path) as store, \
            mock.patch.object(service_module, "now_ms", lambda: collectors.completed_at_ms + decision_delay_ms):
        service = ObserverService(ObserverConfig(), store, collectors)
        for _ in steps:
            decisions.append(service.cycle())
    return decisions


if __name__ == "__main__":  # used by the pinned-checkout test: build_db(path, steps_json)
    import market_observer
    build_db(sys.argv[1], json.loads(sys.argv[2]))
    print(market_observer.__file__)
