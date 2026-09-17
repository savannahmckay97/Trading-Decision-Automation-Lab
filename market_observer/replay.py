"""Chronological historical replay using the production feature and rule functions.

This module deliberately stops short of simulating fills or claiming strategy
performance.  It answers a narrower question: given the information available
at each historical as-of point, what state would the observer have emitted?
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import ObserverConfig
from .engine import (
    advance_breakout_retest,
    assess_gates,
    breakout_quality,
    default_setup_state,
    gate_outcome,
)
from .features import FeatureError, calculate_features
from .util import canonical_json, stable_id


REPLAY_VERSION = "trading-decision-lab-replay-0.1.0"


class ReplayError(ValueError):
    """Raised when replay input would make chronological results unreliable."""


def load_jsonl_snapshots(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield normalized snapshots from a UTF-8 JSON Lines file."""

    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ReplayError(f"line {line_number}: snapshot must be an object")
            yield value


def load_sqlite_snapshots(path: str | Path, research_symbol: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield saved normalized snapshots in their recorded order."""

    uri = f"file:{Path(path).expanduser().resolve()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ReplayError(f"cannot open replay database read-only: {exc}") from exc
    try:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='normalized_snapshots'"
        ).fetchone()
        if table is None:
            raise ReplayError("database has no normalized_snapshots table")
        query = "SELECT payload_json FROM normalized_snapshots"
        params: tuple[Any, ...] = ()
        if research_symbol:
            query += " WHERE research_symbol=?"
            params = (research_symbol,)
        query += " ORDER BY observed_at_ms, rowid"
        for (payload_json,) in db.execute(query, params):
            value = json.loads(payload_json)
            if not isinstance(value, dict):
                raise ReplayError("stored normalized snapshot is not an object")
            yield value
    finally:
        db.close()


class ReplayRunner:
    """Replay snapshots without collectors, credentials, orders, or shared state."""

    def __init__(self, config: ObserverConfig) -> None:
        self.config = replace(config, mode="historical_replay")
        self.config.validate()

    def run(self, snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
        state = default_setup_state()
        decisions: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        previous_close_time: int | None = None
        previous_observed_at: int | None = None
        signal_counts: Counter[str] = Counter()
        sequence_counts: Counter[str] = Counter()

        for index, original in enumerate(snapshots):
            snapshot = deepcopy(original)
            self._validate_identity(snapshot, index)
            observed_at = int(snapshot["observed_at_ms"])
            if previous_observed_at is not None and observed_at < previous_observed_at:
                raise ReplayError(
                    f"snapshot {index}: observed_at_ms moved backwards; input must be chronological"
                )
            previous_observed_at = observed_at

            try:
                features = calculate_features(snapshot, self.config)
                close_time = int(features["completed_candle_close_time_ms"])
                sequence = sequence_status(previous_close_time, close_time, self.config.interval_ms)
                sequence_counts[sequence["status"]] += 1
                if sequence["status"] == "OUT_OF_ORDER":
                    raise ReplayError(
                        f"snapshot {index}: completed candle moved backwards from "
                        f"{previous_close_time} to {close_time}"
                    )
                if sequence["status"] == "IRREGULAR":
                    raise ReplayError(
                        f"snapshot {index}: completed-candle spacing {sequence['delta_ms']}ms "
                        f"is not aligned to {self.config.interval_ms}ms"
                    )
                if sequence["status"] == "GAP":
                    gaps.append({"snapshot_index": index, **sequence})
                    state = default_setup_state()

                gates = assess_gates(snapshot, features, self.config)
                blocked = gate_outcome(gates)
                data_fresh = not any(
                    gate["status"] == "FAIL" and gate["category"] == "DATA_STALE"
                    for gate in gates
                )
                quality = breakout_quality(features, self.config, data_fresh)
                if blocked:
                    signal = blocked
                    state_updated = False
                else:
                    state, signal = advance_breakout_retest(state, features, quality, self.config)
                    state_updated = sequence["status"] != "DUPLICATE"
                decision = self._decision(
                    index=index,
                    snapshot=snapshot,
                    features=features,
                    gates=gates,
                    quality=quality,
                    state=state,
                    signal=signal,
                    state_updated=state_updated,
                    sequence=sequence,
                )
                previous_close_time = close_time
            except FeatureError as exc:
                sequence_counts["FEATURE_INVALID"] += 1
                decision = self._failure(index, snapshot, state, exc)

            signal_counts[decision["signal"]] += 1
            decisions.append(decision)

        baseline = {
            "replay_version": REPLAY_VERSION,
            "configuration": self.config.public_dict(),
            "configuration_id": stable_id("config", self.config.public_dict()),
            "input_snapshot_count": len(decisions),
        }
        return {
            "schema_version": "historical_replay/0.1",
            "replay_id": stable_id(
                "replay",
                {
                    "baseline": baseline,
                    "decision_ids": [item["decision_id"] for item in decisions],
                },
            ),
            "baseline": baseline,
            "summary": {
                "decision_count": len(decisions),
                "signal_counts": dict(sorted(signal_counts.items())),
                "sequence_counts": dict(sorted(sequence_counts.items())),
                "gap_count": len(gaps),
                "missing_completed_bars": sum(item["missing_bars"] for item in gaps),
                "strategy_validated": False,
                "performance_claim_available": False,
            },
            "gaps": gaps,
            "decisions": decisions,
            "limitations": [
                "No fill simulation",
                "No fees, slippage, funding, borrow, or liquidation accounting",
                "No return labels or expectancy calculation",
                "No parameter optimization or out-of-sample validation",
            ],
        }

    def _validate_identity(self, snapshot: dict[str, Any], index: int) -> None:
        required = {"observed_at_ms", "exchange_time_ms", "research_symbol", "executable_pair"}
        missing = sorted(required - snapshot.keys())
        if missing:
            raise ReplayError(f"snapshot {index}: missing identity fields {missing}")
        if snapshot["research_symbol"] != self.config.research_symbol:
            raise ReplayError(
                f"snapshot {index}: expected {self.config.research_symbol}, "
                f"got {snapshot['research_symbol']}"
            )
        if snapshot["executable_pair"] != self.config.executable_pair:
            raise ReplayError(
                f"snapshot {index}: expected {self.config.executable_pair}, "
                f"got {snapshot['executable_pair']}"
            )

    def _decision(
        self,
        *,
        index: int,
        snapshot: dict[str, Any],
        features: dict[str, Any],
        gates: list[dict[str, Any]],
        quality: dict[str, Any],
        state: dict[str, Any],
        signal: str,
        state_updated: bool,
        sequence: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "replay_decision/0.1",
            "replay_version": REPLAY_VERSION,
            "snapshot_index": index,
            "observed_at_ms": int(snapshot["observed_at_ms"]),
            "completed_candle_close_time_ms": int(features["completed_candle_close_time_ms"]),
            "research_symbol": self.config.research_symbol,
            "executable_pair": self.config.executable_pair,
            "signal": signal,
            "action": "NO_ORDER",
            "execution_permission": "DENIED_HISTORICAL_REPLAY",
            "strategy_validated": False,
            "sequence": sequence,
            "setup": {**state, "state_updated": state_updated},
            "breakout_quality": quality,
            "features": features,
            "gates": gates,
        }
        return {"decision_id": stable_id("replay_decision", payload), **payload}

    def _failure(
        self,
        index: int,
        snapshot: dict[str, Any],
        state: dict[str, Any],
        exc: Exception,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "replay_decision/0.1",
            "replay_version": REPLAY_VERSION,
            "snapshot_index": index,
            "observed_at_ms": int(snapshot.get("observed_at_ms", 0)),
            "completed_candle_close_time_ms": None,
            "research_symbol": self.config.research_symbol,
            "executable_pair": self.config.executable_pair,
            "signal": "DATA_STALE",
            "action": "NO_ORDER",
            "execution_permission": "DENIED_HISTORICAL_REPLAY",
            "strategy_validated": False,
            "sequence": {"status": "FEATURE_INVALID", "missing_bars": 0},
            "setup": {**state, "state_updated": False},
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
        }
        return {"decision_id": stable_id("replay_decision", payload), **payload}


def sequence_status(previous_close_ms: int | None, current_close_ms: int, interval_ms: int) -> dict[str, Any]:
    """Classify chronological continuity between two completed candles."""

    if previous_close_ms is None:
        return {"status": "FIRST", "missing_bars": 0, "delta_ms": None}
    delta = current_close_ms - previous_close_ms
    if delta < 0:
        return {"status": "OUT_OF_ORDER", "missing_bars": 0, "delta_ms": delta}
    if delta == 0:
        return {"status": "DUPLICATE", "missing_bars": 0, "delta_ms": 0}
    if delta == interval_ms:
        return {"status": "CONTIGUOUS", "missing_bars": 0, "delta_ms": delta}
    if delta > interval_ms and delta % interval_ms == 0:
        return {
            "status": "GAP",
            "missing_bars": delta // interval_ms - 1,
            "delta_ms": delta,
            "previous_close_time_ms": previous_close_ms,
            "current_close_time_ms": current_close_ms,
        }
    return {
        "status": "IRREGULAR",
        "missing_bars": 0,
        "delta_ms": delta,
        "previous_close_time_ms": previous_close_ms,
        "current_close_time_ms": current_close_ms,
    }


def write_replay(result: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(result) + "\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Trading Decision Automation Lab — historical replay")
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--jsonl", help="normalized-snapshot JSON Lines input")
    source.add_argument("--db", help="observer SQLite database input")
    result.add_argument("--base", default="TAO", help="base asset; TAO by default")
    result.add_argument("--output", required=True, help="immutable replay-result JSON path")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = ObserverConfig(base_asset=args.base.upper(), mode="historical_replay")
    snapshots = (
        load_jsonl_snapshots(args.jsonl)
        if args.jsonl
        else load_sqlite_snapshots(args.db, config.research_symbol)
    )
    result = ReplayRunner(config).run(snapshots)
    write_replay(result, args.output)
    print(
        json.dumps(
            {"replay_id": result["replay_id"], **result["summary"], "output": str(Path(args.output))},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
