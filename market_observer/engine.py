"""Deterministic gates, breakout score and BREAKOUT_RETEST_V1 state machine."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from .config import ObserverConfig
from .util import decimal


STRATEGY_ID = "BREAKOUT_RETEST_V1"
TERMINAL_STATES = {"LONG_ELIGIBLE", "FAILED", "EXPIRED"}


def default_setup_state() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "state": "IDLE",
        "setup_id": None,
        "breakout_resistance": None,
        "breakout_atr": None,
        "breakout_close_time_ms": None,
        "bars_since_breakout": 0,
        "last_evaluated_close_time_ms": None,
        "transition": "INITIALIZED",
    }


def assess_gates(snapshot: dict[str, Any], features: dict[str, Any], config: ObserverConfig) -> list[dict[str, Any]]:
    observed = int(snapshot["observed_at_ms"])
    provenance = snapshot.get("provenance", {})
    gates: list[dict[str, Any]] = []

    def add(rule_id: str, passed: bool, evidence: dict[str, Any], category: str) -> None:
        gates.append(
            {
                "rule_id": rule_id,
                "status": "PASS" if passed else "FAIL",
                "category": category,
                "evidence": evidence,
            }
        )

    missing = snapshot.get("quality", {}).get("critical_missing", [])
    add("T0_REQUIRED_INPUTS", not missing, {"critical_missing": missing}, "DATA_STALE")

    freshness_limits = {
        "binance_time": 5_000,
        "binance_price": 15_000,
        "binance_oi_history": 360_000,
        "binance_taker": 360_000,
        "kraken_ticker": 15_000,
    }
    stale: dict[str, Any] = {}
    for name, limit in freshness_limits.items():
        item = provenance.get(name)
        if not item:
            stale[name] = "missing"
            continue
        basis = item["received_time_ms"] if name == "kraken_ticker" else item.get("event_time_ms")
        if basis is None:
            stale[name] = "missing_event_time"
            continue
        age = observed - int(basis)
        if age < -5_000 or age > limit:
            stale[name] = {"age_ms": age, "limit_ms": limit}
    candle_age = int(snapshot["exchange_time_ms"]) - int(features["completed_candle_close_time_ms"])
    if candle_age < 0 or candle_age > 960_000:
        stale["binance_klines"] = {"closed_candle_age_ms": candle_age, "limit_ms": 960_000}
    add("T0_DATA_FRESHNESS", not stale, {"stale_or_invalid": stale}, "DATA_STALE")

    spread = features.get("spread_bps")
    add(
        "T0_SPREAD_BUDGET",
        spread is not None and spread <= decimal(config.max_spread_bps),
        {"spread_bps": spread, "maximum_bps": config.max_spread_bps},
        "RISK_BLOCKED",
    )
    divergence = features.get("cross_venue_divergence_bps")
    add(
        "T0_VENUE_CONFIRMATION",
        divergence is not None and divergence <= decimal(config.max_cross_venue_divergence_bps),
        {
            "divergence_bps": divergence,
            "maximum_bps": config.max_cross_venue_divergence_bps,
            "research_quote": "USDT",
            "executable_quote": "USD",
            "parity_assumed": False,
        },
        "RISK_BLOCKED",
    )
    add(
        "T0_OBSERVATION_ONLY",
        config.mode in {"observation_only", "historical_replay", "paper"},
        {"mode": config.mode, "orders_available": False},
        "RISK_BLOCKED",
    )
    return gates


def breakout_quality(features: dict[str, Any], config: ObserverConfig, data_fresh: bool) -> dict[str, Any]:
    volume = features["volume_percentile"]
    oi = features["oi_change_15m_pct"]
    taker = features["taker_buy_share"]
    spread = features["spread_bps"]
    volume_component = 2 if volume >= Decimal("0.90") else 1 if volume >= Decimal("0.70") else -1 if volume < Decimal("0.30") else 0
    threshold = decimal(config.minimum_material_change_pct)
    oi_component = 1 if oi >= threshold else -1 if oi <= -threshold else 0
    taker_component = 1 if taker >= Decimal("0.55") else -1 if taker <= Decimal("0.45") else 0
    spread_component = 0
    if spread is None:
        spread_component = -2
    elif spread > decimal(config.max_spread_bps):
        spread_component = -2
    elif spread > decimal(config.max_spread_bps) * Decimal("0.75"):
        spread_component = -1
    stale_component = 0 if data_fresh else -5
    raw = volume_component + oi_component + taker_component + spread_component + stale_component
    score = max(-5, min(5, raw))
    return {
        "score": score,
        "range": [-5, 5],
        "components": {
            "volume": volume_component,
            "open_interest": oi_component,
            "taker_flow": taker_component,
            "spread": spread_component,
            "stale_data": stale_component,
        },
        "calibration_status": "EXPERIMENTAL_UNCALIBRATED",
    }


def gate_outcome(gates: list[dict[str, Any]]) -> str | None:
    if any(gate["status"] == "FAIL" and gate["category"] == "DATA_STALE" for gate in gates):
        return "DATA_STALE"
    if any(gate["status"] == "FAIL" and gate["category"] == "RISK_BLOCKED" for gate in gates):
        return "RISK_BLOCKED"
    return None


def advance_breakout_retest(
    previous_state: dict[str, Any] | None,
    features: dict[str, Any],
    quality: dict[str, Any],
    config: ObserverConfig,
) -> tuple[dict[str, Any], str]:
    state = deepcopy(previous_state or default_setup_state())
    close_time = int(features["completed_candle_close_time_ms"])
    if state.get("last_evaluated_close_time_ms") == close_time:
        return state, _signal_for_state(state["state"])

    if state.get("state") in TERMINAL_STATES:
        state = default_setup_state()
        state["transition"] = "TERMINAL_RESET"

    current_state = state["state"]
    if current_state == "IDLE":
        breakout_threshold = features["resistance"] + decimal(config.breakout_buffer_atr) * features["atr_15m"]
        if features["completed_close"] > breakout_threshold and quality["score"] >= config.minimum_breakout_quality:
            state.update(
                state="BREAKOUT_OBSERVED",
                setup_id=f"{STRATEGY_ID}:{close_time}",
                breakout_resistance=features["resistance"],
                breakout_atr=features["atr_15m"],
                breakout_close_time_ms=close_time,
                bars_since_breakout=0,
                transition="IDLE_TO_BREAKOUT_OBSERVED",
            )
    elif current_state in {"BREAKOUT_OBSERVED", "RETEST_PENDING"}:
        state["bars_since_breakout"] = int(state.get("bars_since_breakout", 0)) + 1
        resistance = decimal(state["breakout_resistance"])
        atr = decimal(state["breakout_atr"])
        failure_level = resistance - decimal(config.failure_buffer_atr) * atr
        if features["completed_close"] < failure_level:
            state.update(state="FAILED", transition=f"{current_state}_TO_FAILED")
        elif state["bars_since_breakout"] > config.expiry_bars:
            state.update(state="EXPIRED", transition=f"{current_state}_TO_EXPIRED")
        else:
            state.update(state="RETEST_PENDING", transition=f"{current_state}_TO_RETEST_PENDING")
            retest_ceiling = resistance + decimal(config.retest_tolerance_atr) * atr
            if (
                features["completed_low"] <= retest_ceiling
                and features["completed_close"] >= resistance
                and features["taker_buy_share"] >= decimal(config.minimum_retest_buy_share)
            ):
                state.update(state="LONG_ELIGIBLE", transition="RETEST_PENDING_TO_LONG_ELIGIBLE")

    state["last_evaluated_close_time_ms"] = close_time
    return state, _signal_for_state(state["state"])


def _signal_for_state(state: str) -> str:
    if state in {"BREAKOUT_OBSERVED", "RETEST_PENDING"}:
        return "WATCH_LONG"
    if state == "LONG_ELIGIBLE":
        return "LONG_ELIGIBLE"
    return "NO_TRADE"
