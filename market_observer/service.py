"""One-cycle orchestration: collect, normalize, evaluate, journal."""

from __future__ import annotations

from typing import Any

from .collectors import PublicCollectors
from .config import ObserverConfig
from .decision_risk import assess_decision_risk
from .engine import STRATEGY_ID, default_setup_state
from .evaluator import evaluate_snapshot
from .features import FeatureError
from .normalization import build_snapshot
from .storage import EventStore
from .util import now_ms, stable_id


class ObserverService:
    VERSION = "trading-decision-lab-observer-0.3.0"

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
        # Saved setup state is only resumed under the rule settings that produced it;
        # operational settings (timeouts, polling, cost assumptions) don't count.
        self.configuration_id = stable_id("state_rules", config.state_rule_dict())

    def cycle(self) -> dict[str, Any]:
        events, errors = self.collectors.collect()
        completed_at = getattr(self.collectors, "completed_at_ms", None)
        if completed_at is None:
            completed_at = now_ms()
        self.store.append_collection(events.values(), errors)
        snapshot_id: str | None = None
        try:
            snapshot = build_snapshot(
                events,
                errors,
                self.config.research_symbol,
                self.config.executable_pair,
                completed_at_ms=completed_at,
            )
            snapshot_id = self.store.append_snapshot(snapshot)
            previous = self._resumable_state()
            evaluation = evaluate_snapshot(snapshot, self.config, previous)

            decision = self._decision(
                signal=evaluation.signal,
                setup=evaluation.state,
                state_updated=evaluation.state_updated,
                snapshot_id=snapshot_id,
                features=evaluation.features,
                quality=evaluation.quality,
                gates=evaluation.gates,
                risk=evaluation.risk,
                errors=[error.record() for error in errors],
            )
            self.store.commit_cycle(
                decision,
                state=evaluation.state if evaluation.state_updated else None,
                strategy_id=STRATEGY_ID,
                configuration_id=self.configuration_id,
            )
            self.store.heartbeat(
                "OK" if evaluation.signal not in {"DATA_STALE", "RISK_BLOCKED"} else "DEGRADED",
                evaluation.signal,
            )
            return decision
        except (FeatureError, KeyError, TypeError, ValueError) as exc:
            decision = self._failure_decision(errors, exc, snapshot_id)
            self.store.append_decision(decision)
            self.store.heartbeat("DEGRADED", str(exc))
            return decision

    def _resumable_state(self) -> dict[str, Any] | None:
        """The saved setup if it was formed under these rule settings. A setup saved
        under other (or unrecorded) rule settings is not resumed: a fresh IDLE state
        carries the visible transition CONFIGURATION_RESET instead."""
        state, configuration_id = self.store.load_setup_state_record(STRATEGY_ID, self.config.research_symbol)
        if state is None or configuration_id == self.configuration_id:
            return state
        reset = default_setup_state()
        reset["transition"] = "CONFIGURATION_RESET"
        return reset

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
        risk: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "market_decision/0.3",
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
            "configuration_id": self.configuration_id,
            "breakout_quality": quality,
            "features": features,
            "gates": gates,
            "risk": risk,
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
        setup = self._resumable_state() or default_setup_state()
        return {
            "schema_version": "market_decision/0.3",
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
            "risk": assess_decision_risk("DATA_STALE", setup, None, self.config),
            "snapshot_id": snapshot_id,
            "collection_errors": [error.record() for error in errors],
            "unknown_or_unimplemented": ["decision inputs unavailable"],
        }
