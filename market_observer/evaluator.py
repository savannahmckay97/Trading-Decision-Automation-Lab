"""Shared deterministic decision evaluation for live observation and replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .asof import validate_snapshot_asof
from .config import ObserverConfig
from .continuity import sequence_status
from .decision_risk import assess_decision_risk
from .engine import (
    advance_breakout_retest,
    assess_gates,
    breakout_quality,
    default_setup_state,
    gate_outcome,
)
from .features import FeatureError, calculate_features


@dataclass(frozen=True)
class Evaluation:
    features: dict[str, Any]
    gates: list[dict[str, Any]]
    quality: dict[str, Any]
    state: dict[str, Any]
    signal: str
    state_updated: bool
    completed_candle_close_time_ms: int
    risk: dict[str, Any]


def evaluate_snapshot(
    snapshot: dict[str, Any],
    config: ObserverConfig,
    previous_state: dict[str, Any] | None,
    *,
    features: dict[str, Any] | None = None,
) -> Evaluation:
    """Evaluate one normalized snapshot without persistence or side effects.

    This is the single production decision path used by both live observation
    and historical replay. Repeated polling of the same completed candle is
    explicitly idempotent and is reported as not updating state.
    """

    validate_snapshot_asof(snapshot)
    resolved_features = features if features is not None else calculate_features(snapshot, config)
    close_time = int(resolved_features["completed_candle_close_time_ms"])
    gates = assess_gates(snapshot, resolved_features, config)
    blocked = gate_outcome(gates)
    data_fresh = not any(
        gate["status"] == "FAIL" and gate["category"] == "DATA_STALE"
        for gate in gates
    )
    quality = breakout_quality(resolved_features, config, data_fresh)
    prior = previous_state or default_setup_state()
    previous_close = prior.get("last_evaluated_close_time_ms")
    if previous_close is not None and (type(previous_close) is not int or previous_close < 0):
        raise FeatureError("invalid persisted last evaluated candle timestamp")
    if previous_close is None and prior.get("state") != "IDLE":
        raise FeatureError("active setup has no last evaluated candle timestamp")
    continuity = sequence_status(previous_close, close_time, config.interval_ms)
    if continuity["status"] in {"OUT_OF_ORDER", "IRREGULAR"}:
        raise FeatureError(f"invalid state candle continuity: {continuity}")
    reset = not blocked and continuity["status"] == "GAP"
    gates.append({
        "rule_id": "T0_STATE_CONTINUITY",
        "status": "PASS",
        "category": "DATA_STALE",
        "evidence": {**continuity, "setup_reset": reset},
    })

    if blocked:
        state = prior
        signal = blocked
        state_updated = False
    else:
        # Unseen candles may have invalidated the old hypothesis. Do not bridge
        # them, infer transitions, or count them as a single retest bar.
        if reset:
            prior = default_setup_state()
            prior["transition"] = "GAP_RESET"
        already_evaluated = prior.get("last_evaluated_close_time_ms") == close_time
        state, signal = advance_breakout_retest(prior, resolved_features, quality, config)
        state_updated = not already_evaluated

    return Evaluation(
        features=resolved_features,
        gates=gates,
        quality=quality,
        state=state,
        signal=signal,
        state_updated=state_updated,
        completed_candle_close_time_ms=close_time,
        risk=assess_decision_risk(signal, state, resolved_features, config, snapshot),
    )

