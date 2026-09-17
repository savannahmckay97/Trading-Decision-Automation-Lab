"""One-cycle orchestration: collect, normalize, calculate, gate, journal."""

from __future__ import annotations

from typing import Any

from .collectors import PublicCollectors
from .config import ObserverConfig
from .engine import (
    STRATEGY_ID,
    advance_breakout_retest,
    assess_gates,
    breakout_quality,
    default_setup_state,
    gate_outcome,
)
from .features import FeatureError, calculate_features
from .normalization import build_snapshot
from .storage import EventStore
from .util import now_ms


class ObserverService:
    VERSION = "trading-decision-lab-observer-0.1.0"

    def __init__(
        self,
        config: ObserverConfig,
        store: EventStore,
        collectors: PublicCollectors | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.store = store
        self.collectors = collectors or PublicCollectors(config)

    def cycle(self) -> dict[str, Any]:
        events, errors = self.collectors.collect()
        self.store.append_collection(events.values(), errors)
        snapshot_id: str | None = None
        try:
            snapshot = build_snapshot(
                events,
                errors,
                self.config.research_symbol,
                self.config.executable_pair,
            )
            snapshot_id = self.store.append_snapshot(snapshot)
            features = calculate_features(snapshot, self.config)
            gates = assess_gates(snapshot, features, self.config)
            blocked = gate_outcome(gates)
            data_fresh = not any(
                gate["status"] == "FAIL" and gate["category"] == "DATA_STALE" for gate in gates
            )
            quality = breakout_quality(features, self.config, data_fresh)
            previous = self.store.load_setup_state(STRATEGY_ID, self.config.research_symbol)
            if blocked:
                setup = previous or default_setup_state()
                signal = blocked
                state_updated = False
            else:
                setup, signal = advance_breakout_retest(previous, features, quality, self.config)
                self.store.save_setup_state(STRATEGY_ID, self.config.research_symbol, setup)
                state_updated = True
            decision = self._decision(
                signal=signal,
                setup=setup,
                state_updated=state_updated,
                snapshot_id=snapshot_id,
                features=features,
                quality=quality,
                gates=gates,
                errors=[error.record() for error in errors],
            )
            self.store.append_decision(decision)
            self.store.heartbeat("OK" if not blocked else "DEGRADED", signal)
            return decision
        except (FeatureError, KeyError, TypeError, ValueError) as exc:
            decision = self._failure_decision(errors, exc, snapshot_id)
            self.store.append_decision(decision)
            self.store.heartbeat("DEGRADED", str(exc))
            return decision

    def _decision(
        self,
        *,
        signal: str,
        setup: dict[str, Any],
        state_updated: bool,
        snapshot_id: str,
        features: dict[str, Any],
        quality: dict[str, Any],
        gates: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "market_decision/0.1",
            "observer_version": self.VERSION,
            "decision_time_ms": now_ms(),
            "mode": self.config.mode,
            "research_symbol": self.config.research_symbol,
            "executable_pair": self.config.executable_pair,
            "signal": signal,
            "action": "NO_ORDER",
            "execution_permission": "DENIED_OBSERVATION_ONLY",
            "strategy_validated": False,
            "setup": {**setup, "state_updated": state_updated},
            "breakout_quality": quality,
            "features": features,
            "gates": gates,
            "snapshot_id": snapshot_id,
            "collection_errors": errors,
            "unknown_or_unimplemented": [
                "scheduled-event coverage",
                "account equity and portfolio heat",
                "fee and borrow truth",
                "backtested net expectancy",
                "paper-fill simulation",
            ],
        }

    def _failure_decision(self, errors: list, exc: Exception, snapshot_id: str | None) -> dict[str, Any]:
        setup = self.store.load_setup_state(STRATEGY_ID, self.config.research_symbol) or default_setup_state()
        return {
            "schema_version": "market_decision/0.1",
            "observer_version": self.VERSION,
            "decision_time_ms": now_ms(),
            "mode": self.config.mode,
            "research_symbol": self.config.research_symbol,
            "executable_pair": self.config.executable_pair,
            "signal": "DATA_STALE",
            "action": "NO_ORDER",
            "execution_permission": "DENIED_OBSERVATION_ONLY",
            "strategy_validated": False,
            "setup": {**setup, "state_updated": False},
            "breakout_quality": None,
            "features": None,
            "gates": [
                {
                    "rule_id": "T0_FAIL_CLOSED",
                    "status": "FAIL",
                    "category": "DATA_STALE",
                    "evidence": {"error": f"{type(exc).__name__}: {exc}"},
                }
            ],
            "snapshot_id": snapshot_id,
            "collection_errors": [error.record() for error in errors],
            "unknown_or_unimplemented": ["decision inputs unavailable"],
        }
