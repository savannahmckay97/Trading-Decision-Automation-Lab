"""Per-decision risk contract: invalidation and per-unit risk, never a size.

This is a pure function of the evaluator's own outputs. It describes what the
active setup's invalidation implies at the research venue; it does not choose
an entry, quantity or exit, translate prices to the executable venue, or grant
execution permission. Account sizing and portfolio heat remain out of scope.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .config import ObserverConfig
from .util import decimal


RISK_CONTRACT_VERSION = "decision_risk/0.1"
TEN_THOUSAND = Decimal("10000")
ACTIVE_SETUP_STATES = {"BREAKOUT_OBSERVED", "RETEST_PENDING", "LONG_ELIGIBLE"}
BLOCKED_SIGNALS = {"DATA_STALE", "RISK_BLOCKED"}

# Stated on every record so a consumer cannot mistake the figures for more.
NOT_INCLUDED = [
    "account equity, quantity and portfolio heat",
    "exchange fees and slippage",
    "funding and borrow",
    "gap or slippage through invalidation",
    "executable-venue (Kraken) stop price translation",
    "profit target or reward estimate",
]


def assess_decision_risk(
    signal: str,
    setup: dict[str, Any],
    features: dict[str, Any] | None,
    config: ObserverConfig,
) -> dict[str, Any]:
    """Return the risk contract for one evaluated decision.

    Status is AVAILABLE only for an active setup evaluated on unblocked inputs.
    Blocked cycles withhold figures rather than reporting them from inputs that
    failed a gate; malformed setup values fail closed as UNAVAILABLE.
    """

    base = {
        "contract_version": RISK_CONTRACT_VERSION,
        "strategy_id": setup.get("strategy_id"),
        "setup_id": setup.get("setup_id"),
        "sizing": "NOT_COMPUTED_ACCOUNT_STATE_UNKNOWN",
        "execution_permission": "NONE",
        "not_included": NOT_INCLUDED,
    }
    if signal in BLOCKED_SIGNALS:
        return {**base, "status": "WITHHELD_BLOCKED", "reason": signal}
    if setup.get("state") not in ACTIVE_SETUP_STATES:
        return {**base, "status": "NOT_APPLICABLE", "reason": f"setup_state_{setup.get('state')}"}
    if features is None:
        return {**base, "status": "UNAVAILABLE", "reason": "features_missing"}

    try:
        resistance = decimal(setup["breakout_resistance"])
        atr = decimal(setup["breakout_atr"])
        breakout_close_ms = int(setup["breakout_close_time_ms"])
        bars_since = int(setup.get("bars_since_breakout", 0))
        reference = decimal(features["completed_close"])
        close_time_ms = int(features["completed_candle_close_time_ms"])
        failure_buffer = decimal(config.failure_buffer_atr)
    except (KeyError, TypeError, ValueError) as exc:
        return {**base, "status": "UNAVAILABLE", "reason": f"invalid_setup_or_features: {exc}"}

    invalidation = resistance - failure_buffer * atr
    if atr <= 0 or reference <= 0 or invalidation <= 0:
        return {**base, "status": "UNAVAILABLE", "reason": "non_positive_price_or_atr"}
    if reference <= invalidation:
        # The state machine should already have failed this setup; never report
        # a negative or zero risk distance as if it were usable.
        return {**base, "status": "UNAVAILABLE", "reason": "reference_at_or_below_invalidation"}

    distance = reference - invalidation
    distance_bps = distance / reference * TEN_THOUSAND
    spread = features.get("spread_bps")
    spread_bps = decimal(spread) if spread is not None else None
    if setup["state"] == "LONG_ELIGIBLE":
        last_evaluable_close_ms = close_time_ms
    else:
        last_evaluable_close_ms = breakout_close_ms + config.expiry_bars * config.interval_ms

    return {
        **base,
        "status": "AVAILABLE",
        "setup_state": setup["state"],
        "venue": {
            "price_venue": "binance_usds_m_futures",
            "symbol": config.research_symbol,
            "quote": config.research_quote,
            "executable_pair": config.executable_pair,
            "cross_venue_parity_assumed": False,
        },
        "reference_price": {
            "value": reference,
            "kind": "RESEARCH_COMPLETED_CLOSE_NOT_AN_ENTRY",
            "candle_close_time_ms": close_time_ms,
        },
        "invalidation": {
            "level": invalidation,
            "rule": "COMPLETED_CLOSE_BELOW",
            "interval": config.interval,
            "derivation": {
                "breakout_resistance": resistance,
                "breakout_atr": atr,
                "failure_buffer_atr": failure_buffer,
            },
            "intrabar_touch_invalidates": False,
            "note": "Setup failure needs a completed research close below the level; "
            "the paper simulator exits on an intrabar low touch instead.",
        },
        "per_unit_risk": {
            "distance": distance,
            "distance_quote": config.research_quote,
            "distance_bps_of_reference": distance_bps,
            "distance_atr": distance / atr,
            "executable_spread_bps": spread_bps,
            "distance_plus_spread_bps": distance_bps + spread_bps if spread_bps is not None else None,
            "spread_basis": "KRAKEN_SIGNAL_TIME_QUOTE_ROUND_TRIP_PROXY",
            "gap_loss_bounded": False,
        },
        "setup_window": {
            "breakout_close_time_ms": breakout_close_ms,
            "bars_since_breakout": bars_since,
            "last_evaluable_close_time_ms": last_evaluable_close_ms,
        },
        "reward_reference": "NOT_DEFINED_BY_STRATEGY",
    }
