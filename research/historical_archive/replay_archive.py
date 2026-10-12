"""Replay the production BREAKOUT_RETEST_V1 rule candle by candle over historical series.

Same assumptions as research/setup_frequency/history.py, so results are comparable:
derivatives aligned to each candle (the three composing 5m taker buckets; OI at the close
boundary), decision 4 minutes after the close (after the slowest taker publication seen live),
a fixed 3.35 bps Kraken spread with zero cross-venue divergence, and no freshness gates.
Missing taker buckets or OI not aligned to the boundary reset the setup (fail closed), as a
gap does live. Uses only production functions; changes no parameter.

This module reads no outcome. It returns decisions and per-candle states only.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import Decimal

from market_observer.config import ObserverConfig
from market_observer.decision_risk import assess_decision_risk
from market_observer.engine import advance_breakout_retest, breakout_quality
from market_observer.features import FeatureError, calculate_features

SPREAD = Decimal("0.000335")  # live-observed Kraken spread (3.35 bps): ASSUMPTION
DECISION_DELAY_MS = 240_000
OI_LOOKBACK_MS = 4 * 3_600_000


def replay(bars, oi_rows, taker, deriv_start_ms, config=None, start_ms=None, end_ms=None):
    """Return (counts, per_candle, decisions).

    per_candle: one (close_time_ms, state, signal, quality_score) per evaluated candle.
    decisions: LONG_ELIGIBLE candles with their risk contract.
    start_ms/end_ms limit which candles are evaluated (by open time), after warm-up.
    """
    config = config or ObserverConfig()
    oi_ts = [r["timestamp_ms"] for r in oi_rows]
    counts, per_candle, decisions, state = Counter(), [], [], None
    for i, b in enumerate(bars):
        o, boundary = b["open_time_ms"], b["open_time_ms"] + 900_000
        if o < deriv_start_ms + 3_600_000 or i < 100:
            continue  # derivative warm-up (60m OI lookback) and 97-bar candle warm-up
        if (start_ms is not None and o < start_ms) or (end_ms is not None and o >= end_ms):
            continue
        counts["bars_considered"] += 1
        composing = [taker.get(o + k * 300_000) for k in range(3)]
        if None in composing:
            counts["skipped_missing_taker_bucket"] += 1
            state = None
            continue
        lo, hi = bisect_left(oi_ts, boundary - OI_LOOKBACK_MS), bisect_right(oi_ts, boundary)
        oi_window = oi_rows[lo:hi]
        if not oi_window or oi_window[-1]["timestamp_ms"] != boundary:
            counts["skipped_oi_not_aligned"] += 1
            state = None
            continue
        close = b["close"]
        snap = {
            "exchange_time_ms": boundary + DECISION_DELAY_MS,
            "candles_15m": bars[max(0, i - 149): i + 1],
            "open_interest_5m": oi_window,
            "taker_flow_5m": composing,
            "funding_history": [],
            "positioning": {"global": [], "top_accounts": [], "top_positions": []},
            "market": {"binance_last_price": close, "kraken_last_price": close,
                       "kraken_bid_price": close * (1 - SPREAD / 2), "kraken_ask_price": close * (1 + SPREAD / 2)},
        }
        try:
            features = calculate_features(snap, config)
        except FeatureError as exc:
            counts[f"skipped_feature_error: {exc}"] += 1
            state = None
            continue
        quality = breakout_quality(features, config, True)
        state, signal = advance_breakout_retest(state, features, quality, config)
        counts[f"transition:{state['transition']}"] += 1
        per_candle.append((b["close_time_ms"], state["state"], signal, quality["score"]))
        if signal == "LONG_ELIGIBLE":
            decisions.append({"bar_index": i, "close_time_ms": b["close_time_ms"], "setup_id": state["setup_id"],
                              "risk": assess_decision_risk(signal, state, features, config, snap)})
    return counts, per_candle, decisions
