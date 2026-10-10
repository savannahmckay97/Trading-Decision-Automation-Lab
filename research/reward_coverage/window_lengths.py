"""Reward-reference coverage by 15m search-window length (availability and distance only; no outcomes).

Usage: window_lengths.py <backfill_30d dir> <klines_long/klines.json>."""
import json, sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup_frequency"))
from history import replay
from market_observer.config import ObserverConfig
from market_observer.reward_reference import assess_reward_reference
bars, *_, decisions = replay(sys.argv[1])
long = [{"open_time_ms": r[0], "close_time_ms": r[6], "open": Decimal(r[1]), "high": Decimal(r[2]),
         "low": Decimal(r[3]), "close": Decimal(r[4]), "volume": Decimal(r[5])}
        for r in json.loads(Path(sys.argv[2]).read_text())]
idx = {b["close_time_ms"]: i for i, b in enumerate(long)}
for n in (144, 499, 999, 1499):
    cfg = replace(ObserverConfig(), candle_limit=1500, reward_search_bars=n)
    cfg.validate()
    out = []
    for d in decisions:
        r = d["risk"]; i = idx[d["close_time_ms"]]
        snap = {"exchange_time_ms": d["close_time_ms"] + 240_001, "candles_15m": long[: i + 1]}
        res = assess_reward_reference(
            snap, reference=r["reference_price"]["value"], reference_close_time_ms=d["close_time_ms"],
            stop_distance=r["per_unit_risk"]["distance"], atr=r["invalidation"]["derivation"]["breakout_atr"],
            breakout_close_time_ms=r["setup_window"]["breakout_close_time_ms"], modelled_round_trip_bps=None, config=cfg)
        out.append(res)
    avail = [o for o in out if o["status"] == "AVAILABLE"]
    dist = sorted(round(float(o["gross_reward"]["atr"]), 1) for o in avail)
    age = sorted(o["source"]["bars_before_reference"] for o in avail)
    print(f"window {n:5d} bars ({n/96:4.1f} d): target available {len(avail):2d}/17; reward in ATR {dist}; pivot age (bars) {age}")
