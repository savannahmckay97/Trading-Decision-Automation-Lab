"""Replay the production BREAKOUT_RETEST_V1 rule over downloaded Binance history.

Diagnostic only, not a TDAL dataset artifact. Assumptions (see
docs/EVALUATION_READINESS.md): derivatives aligned to each candle (the three
composing taker buckets; OI at the close boundary), decision ~3 min after the
close, a fixed 3.35 bps Kraken spread with zero divergence, and no freshness
gates.
"""
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from market_observer.config import ObserverConfig
from market_observer.decision_risk import assess_decision_risk
from market_observer.engine import advance_breakout_retest, breakout_quality
from market_observer.features import FeatureError, calculate_features

SPREAD = Decimal("0.000335")  # live-observed Kraken spread (3.35 bps): ASSUMPTION


def load(data_dir):
    d = Path(data_dir)
    meta = json.loads((d / "meta.json").read_text())
    bars = [{"open_time_ms": r[0], "close_time_ms": r[6], "open": Decimal(r[1]), "high": Decimal(r[2]),
             "low": Decimal(r[3]), "close": Decimal(r[4]), "volume": Decimal(r[5])}
            for r in json.loads((d / "klines.json").read_text())]
    bars = [b for b in bars if b["close_time_ms"] < meta["server_time_ms"]]
    oi = [{"timestamp_ms": int(r["timestamp"]), "open_interest": Decimal(r["sumOpenInterest"])}
          for r in json.loads((d / "oi_hist.json").read_text())]
    taker = {int(r["timestamp"]): {"timestamp_ms": int(r["timestamp"]), "buy_volume": Decimal(r["buyVol"]),
                                   "sell_volume": Decimal(r["sellVol"])}
             for r in json.loads((d / "taker.json").read_text())}
    return meta, bars, oi, taker


def replay(data_dir, config=None):
    """Return (bars, counts, quality_hist, transitions, decisions); decisions carry bar index and risk."""
    config = config or ObserverConfig()
    meta, bars, oi_rows, taker = load(data_dir)
    counts, transitions, quality_hist = Counter(), Counter(), Counter()
    state, decisions = None, []
    for i, b in enumerate(bars):
        o, boundary = b["open_time_ms"], b["open_time_ms"] + 900_000
        if o < meta["deriv_start_ms"] + 3_600_000 or i < 100:
            continue  # derivative warmup (60m OI lookback) and 97-bar candle warmup
        counts["bars_considered"] += 1
        composing = [taker.get(o + k * 300_000) for k in range(3)]
        if None in composing:
            counts["skipped_missing_taker_bucket"] += 1; state = None; continue
        close = b["close"]
        snap = {
            "exchange_time_ms": boundary + 240_000,  # after the slowest taker publication seen (194 s)
            "candles_15m": bars[max(0, i - 149): i + 1],
            "open_interest_5m": [r for r in oi_rows if boundary - 4 * 3_600_000 <= r["timestamp_ms"] <= boundary],
            "taker_flow_5m": composing, "funding_history": [],
            "positioning": {"global": [], "top_accounts": [], "top_positions": []},
            "market": {"binance_last_price": close, "kraken_last_price": close,
                       "kraken_bid_price": close * (1 - SPREAD / 2), "kraken_ask_price": close * (1 + SPREAD / 2)},
        }
        if not snap["open_interest_5m"] or snap["open_interest_5m"][-1]["timestamp_ms"] != boundary:
            counts["skipped_oi_not_aligned"] += 1; state = None; continue
        try:
            features = calculate_features(snap, config)
        except FeatureError as exc:
            counts[f"skipped_feature_error: {exc}"] += 1; state = None; continue
        quality = breakout_quality(features, config, True)
        if features["completed_close"] > features["resistance"] + Decimal(str(config.breakout_buffer_atr)) * features["atr_15m"]:
            quality_hist[quality["score"]] += 1
        state, signal = advance_breakout_retest(state, features, quality, config)
        transitions[state["transition"]] += 1
        if signal == "LONG_ELIGIBLE":
            decisions.append({"bar_index": i, "close_time_ms": b["close_time_ms"],
                              "risk": assess_decision_risk(signal, state, features, config, snap)})
    return bars, counts, quality_hist, transitions, decisions
