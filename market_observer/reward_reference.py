"""Causal overhead reward reference: nearest confirmed swing high above entry.

Descriptive opportunity-to-risk only. The level is not a take-profit order, an
exit rule or evidence of expected reward, and nothing here feeds eligibility.

Why not ``features.resistance``: it is the maximum high of the 32 bars before
the current one. At a breakout every high in that window is below the close,
so it is never overhead; after the breakout it becomes the setup's running
high, which has not been confirmed as a rejection. See
``docs/REWARD_REFERENCE.md``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .config import ObserverConfig
from .features import FeatureError, completed_candle_rows, validate_candle_window
from .util import decimal


TEN_THOUSAND = Decimal("10000")
METHOD = "CONFIRMED_SWING_HIGH"


def assess_reward_reference(
    snapshot: dict[str, Any],
    *,
    reference: Decimal,
    reference_close_time_ms: int,
    stop_distance: Decimal,
    atr: Decimal,
    breakout_close_time_ms: int,
    modelled_round_trip_bps: Decimal | None,
    config: ObserverConfig,
) -> dict[str, Any]:
    k = config.reward_pivot_confirmation_bars
    base = {
        "role": "DESCRIPTIVE_OPPORTUNITY_NOT_TAKE_PROFIT",
        "method": METHOD,
        "confirmation_bars": k,
        "max_search_bars": config.reward_search_bars,
    }
    try:
        window, truncated = _search_window(snapshot, config)
    except (FeatureError, KeyError, TypeError, ValueError) as exc:
        return {**base, "status": "TARGET_UNAVAILABLE", "reason": f"INVALID_SEARCH_WINDOW: {exc}"}
    if not window or int(window[-1]["close_time_ms"]) != reference_close_time_ms:
        return {**base, "status": "TARGET_UNAVAILABLE", "reason": "SEARCH_WINDOW_NOT_ANCHORED_TO_REFERENCE"}

    search = {
        "first_close_time_ms": int(window[0]["close_time_ms"]),
        "last_close_time_ms": int(window[-1]["close_time_ms"]),
        "bars": len(window),
        "truncated_by_gap": truncated,
    }
    highs = [decimal(row["high"]) for row in window]
    pivots = [
        index
        for index in range(k, len(window) - k)
        # Strict on the left, non-strict on the right: a flat top yields one
        # pivot, its earliest bar. Bar index + k must already be complete,
        # which range() guarantees because the window ends at the reference bar.
        if all(highs[index] > highs[j] for j in range(index - k, index))
        and all(highs[index] >= highs[j] for j in range(index + 1, index + k + 1))
    ]
    above = [index for index in pivots if highs[index] > reference]
    unconfirmed = _unconfirmed_overhead(window, highs, k, reference)
    context = {
        **base,
        "search_window": search,
        "confirmed_pivots_in_window": len(pivots),
        "unconfirmed_overhead_high": unconfirmed,
    }
    if not above:
        return {**context, "status": "TARGET_UNAVAILABLE", "reason": "NO_CONFIRMED_LEVEL_ABOVE_REFERENCE_IN_WINDOW"}

    level = min(highs[index] for index in above)
    at_level = [index for index in above if highs[index] == level]
    pivot = max(at_level)  # most recent pivot at the selected level
    row = window[pivot]
    distance = level - reference
    reward_bps = distance / reference * TEN_THOUSAND
    risk_bps = stop_distance / reference * TEN_THOUSAND
    if modelled_round_trip_bps is None:
        adjusted, adjusted_basis = None, "UNAVAILABLE_COSTS_INCOMPLETE"
    else:
        adjusted = (reward_bps - modelled_round_trip_bps) / (risk_bps + modelled_round_trip_bps)
        adjusted_basis = "PARTIAL_EXCLUDES_FUNDING_AND_GAP"
    return {
        **context,
        "status": "AVAILABLE",
        "level": level,
        "source": {
            "pivot_open_time_ms": int(row["open_time_ms"]),
            "pivot_close_time_ms": int(row["close_time_ms"]),
            "confirmed_by_close_time_ms": int(window[pivot + k]["close_time_ms"]),
            "bars_before_reference": len(window) - 1 - pivot,
            "formed_during_setup": int(row["close_time_ms"]) >= breakout_close_time_ms,
            "pivot_count_at_level": len(at_level),
        },
        "target_beyond_unconfirmed_high": unconfirmed is not None and unconfirmed["level"] < level,
        "gross_reward": {
            "distance": distance,
            "bps_of_reference": reward_bps,
            "atr": distance / atr,
        },
        "gross_opportunity_to_risk": distance / stop_distance,
        "ratio_basis": "GROSS_PRICE_DISTANCES_NO_COSTS",
        "cost_adjusted_opportunity_to_risk": adjusted,
        "cost_adjusted_basis": adjusted_basis,
    }


def _search_window(snapshot: dict[str, Any], config: ObserverConfig) -> tuple[list[dict[str, Any]], bool]:
    """Longest contiguous run of completed candles ending at the newest one.

    A gap shortens the search rather than failing it, and is reported, so the
    absence of a level is always qualified by how far back was searched.
    Duplicate or invalid candles still fail closed.
    """

    rows = completed_candle_rows(snapshot)[-config.reward_search_bars :]
    start = 0
    for index in range(len(rows) - 1, 0, -1):
        delta = int(rows[index]["open_time_ms"]) - int(rows[index - 1]["open_time_ms"])
        if delta > config.interval_ms:
            start = index
            break
    window = rows[start:]
    validate_candle_window(window, config.interval_ms)
    return window, start > 0


def _unconfirmed_overhead(
    window: list[dict[str, Any]], highs: list[Decimal], k: int, reference: Decimal
) -> dict[str, Any] | None:
    """Highest recent high that cannot be confirmed yet, if it is overhead.

    The final k bars cannot be pivots until more bars close. Their highs are
    disclosed, not used as the target: a single bar's upper wick would
    otherwise define the reward.
    """

    recent = range(max(0, len(window) - k), len(window))
    candidates = [index for index in recent if highs[index] > reference]
    if not candidates:
        return None
    index = max(candidates, key=lambda i: (highs[i], i))
    return {"level": highs[index], "close_time_ms": int(window[index]["close_time_ms"])}
