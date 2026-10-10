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


RISK_CONTRACT_VERSION = "decision_risk/0.2"
TEN_THOUSAND = Decimal("10000")
ACTIVE_SETUP_STATES = {"BREAKOUT_OBSERVED", "RETEST_PENDING", "LONG_ELIGIBLE"}
BLOCKED_SIGNALS = {"DATA_STALE", "RISK_BLOCKED"}

# Stated on every record so a consumer cannot mistake the figures for more.
NOT_INCLUDED = [
    "account equity, quantity and portfolio heat",
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
    try:
        costs = _cost_assumptions(features.get("spread_bps"), config)
    except ValueError as exc:
        return {**base, "status": "UNAVAILABLE", "reason": f"invalid_cost_input: {exc}"}
    modelled = costs["modelled_round_trip_bps"]
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
            "distance_plus_modelled_costs_bps": distance_bps + modelled if modelled is not None else None,
            "gap_loss_bounded": False,
        },
        "costs": costs,
        "setup_window": {
            "breakout_close_time_ms": breakout_close_ms,
            "bars_since_breakout": bars_since,
            "last_evaluable_close_time_ms": last_evaluable_close_ms,
        },
        "reward_reference": "NOT_DEFINED_BY_STRATEGY",
    }


def _cost_assumptions(spread: Any, config: ObserverConfig) -> dict[str, Any]:
    """Keep each cost component separate; never treat an unknown as zero.

    Conventions match market_observer.paper: the spread is a round-trip proxy
    (half per side) and fee/slippage are per side, so a round trip pays each
    twice. The modelled total exists only when every modelled component is
    known, and it always excludes funding and gap-through.
    """

    spread_bps = decimal(spread) if spread is not None else None
    if spread_bps is not None and spread_bps < 0:
        raise ValueError("negative spread")

    def per_side(value: Any, source: str) -> dict[str, Any]:
        if value is None:
            return {"per_side_bps": None, "round_trip_bps": None, "status": "NOT_SUPPLIED"}
        amount = decimal(value)
        if amount < 0:
            raise ValueError(f"negative {source}")
        return {"per_side_bps": amount, "round_trip_bps": amount * 2, "status": "CONFIGURED_ASSUMPTION"}

    components = {
        "spread": {
            "round_trip_bps": spread_bps,
            "status": "OBSERVED_SIGNAL_TIME_PROXY" if spread_bps is not None else "UNAVAILABLE",
            "basis": "KRAKEN_QUOTE_AT_SIGNAL_TIME",
        },
        "fees": per_side(config.fee_bps_per_side, "fee"),
        "slippage": per_side(config.slippage_bps_per_side, "slippage"),
        "funding": {"status": "NOT_MODELLED"},
    }
    missing = [
        name
        for name in ("spread", "fees", "slippage")
        if components[name]["round_trip_bps"] is None
    ]
    if missing:
        total = None
        basis = "INCOMPLETE_MISSING_" + "_".join(name.upper() for name in missing)
    else:
        total = sum(components[name]["round_trip_bps"] for name in ("spread", "fees", "slippage"))
        basis = "SPREAD_FEES_SLIPPAGE_ADDITIVE_BPS_EXCLUDES_FUNDING_AND_GAP"
    return {**components, "modelled_round_trip_bps": total, "modelled_basis": basis}
