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

from .asof import AsOfError, validate_snapshot_asof
from .config import ObserverConfig
from .continuity import sequence_status
from .decision_risk import assess_decision_risk
from .engine import default_setup_state
from .evaluator import evaluate_snapshot
from .features import FeatureError, calculate_features
from .snapshots import SnapshotError, decode_snapshot, load_snapshot_json
from .util import canonical_json, stable_id


REPLAY_VERSION = "trading-decision-lab-replay-0.4.0"


class ReplayError(ValueError):
    """Raised when replay input would make chronological results unreliable."""


def load_jsonl_snapshots(path: str | Path, *, strict: bool = True) -> Iterator[dict[str, Any]]:
    """Yield normalized snapshots from a UTF-8 JSON Lines file.

    ``strict=False`` yields a snapshot that fails the structural contract as raw JSON,
    for a replay that fails closed on it (``invalid_snapshots="fail_closed"``).
    """

    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = load_snapshot_json(line)
            except SnapshotError as exc:
                if strict:
                    raise ReplayError(f"invalid JSON on line {line_number}: {exc}") from exc
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ReplayError(f"line {line_number}: snapshot must be an object")
            yield value


def load_sqlite_snapshots(
    path: str | Path, research_symbol: str | None = None, *, strict: bool = True
) -> Iterator[dict[str, Any]]:
    """Yield saved normalized snapshots in their recorded order (``strict`` as for JSONL)."""

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
            try:
                value = load_snapshot_json(payload_json)
            except SnapshotError as exc:
                if strict:
                    raise ReplayError(f"invalid stored snapshot: {exc}") from exc
                value = json.loads(payload_json)
            except json.JSONDecodeError as exc:
                raise ReplayError(f"invalid stored snapshot: {exc}") from exc
            if not isinstance(value, dict):
                raise ReplayError("stored normalized snapshot is not an object")
            yield value
    finally:
        db.close()


class _InvalidSnapshot(ValueError):
    """A snapshot that breaks the structural contract, under the fail_closed policy."""


def _failure_status(exc: Exception) -> str:
    if isinstance(exc, AsOfError):
        return "ASOF_INVALID"
    if isinstance(exc, _InvalidSnapshot):
        return "SNAPSHOT_INVALID"
    return "FEATURE_INVALID"


def bind_replay_to_snapshots(snapshots: list[dict[str, Any]], replay_result: dict[str, Any]) -> None:
    """Raise ReplayError unless ``replay_result`` is exactly the replay of these snapshots.

    Rebuilds the replay from the (already verified) snapshots with the configuration the
    replay records, and requires the same configuration, replay ID and decision content.
    A replay of another dataset, symbol or configuration, or with edited decisions, fails.
    """
    baseline = replay_result.get("baseline")
    if not isinstance(baseline, dict) or not isinstance(baseline.get("configuration"), dict):
        raise ReplayError("replay result has no recorded configuration")
    if baseline.get("replay_version") != REPLAY_VERSION:
        raise ReplayError(f"replay was produced by {baseline.get('replay_version')}; this code is "
                          f"{REPLAY_VERSION}. Rebuild the replay, or bind it using the code that made it")
    recorded = dict(baseline["configuration"])
    recorded.pop("research_symbol", None)
    recorded.pop("executable_pair", None)
    try:
        config = ObserverConfig(**recorded)
        config.validate()
    except (TypeError, ValueError) as exc:
        raise ReplayError(f"replay configuration is invalid: {exc}") from exc
    if stable_id("config", config.public_dict()) != baseline.get("configuration_id"):
        raise ReplayError("replay configuration does not match its configuration_id")
    try:
        runner = ReplayRunner(config, invalid_snapshots=baseline.get("invalid_snapshot_policy", "abort"))
    except ValueError as exc:
        raise ReplayError(str(exc)) from exc
    rebuilt = runner.run(snapshots)
    if rebuilt["replay_id"] != replay_result.get("replay_id"):
        raise ReplayError("replay does not match the verified dataset")
    if json.loads(canonical_json(rebuilt["decisions"])) != json.loads(canonical_json(replay_result.get("decisions"))):
        raise ReplayError("replay decisions differ from a replay of the verified dataset")


class ReplayRunner:
    """Replay snapshots without collectors, credentials, orders, or shared state."""

    def __init__(self, config: ObserverConfig, *, invalid_snapshots: str = "abort") -> None:
        """``invalid_snapshots``: "abort" (default) stops on a snapshot that breaks the
        structural contract, treating it as a corrupted dataset. "fail_closed" records a
        DATA_STALE decision for it and continues, as the live service does, so a recorded
        segment with one malformed response stays replayable."""
        if invalid_snapshots not in {"abort", "fail_closed"}:
            raise ValueError("invalid_snapshots must be 'abort' or 'fail_closed'")
        self.config = replace(config, mode="historical_replay")
        self.config.validate()
        self.invalid_snapshots = invalid_snapshots

    def run(self, snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
        state = default_setup_state()
        decisions: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        previous_close_time: int | None = None
        previous_observed_at: int | None = None
        previous_exchange_time: int | None = None
        signal_counts: Counter[str] = Counter()
        sequence_counts: Counter[str] = Counter()

        for index, original in enumerate(snapshots):
            snapshot = deepcopy(original)
            self._validate_identity(snapshot, index)
            observed_at = snapshot.get("observed_at_ms")
            exchange_time = snapshot.get("exchange_time_ms")
            if type(observed_at) is int:
                if previous_observed_at is not None and observed_at < previous_observed_at:
                    raise ReplayError(f"snapshot {index}: observed_at_ms moved backwards; input must be chronological")
                previous_observed_at = observed_at
            if type(exchange_time) is int:
                if previous_exchange_time is not None and exchange_time < previous_exchange_time:
                    raise ReplayError(f"snapshot {index}: exchange_time_ms moved backwards")
                previous_exchange_time = exchange_time
            try:
                validate_snapshot_asof(snapshot)
                try:
                    snapshot = decode_snapshot(snapshot, require_complete=True)
                except SnapshotError as exc:
                    if self.invalid_snapshots == "abort":
                        raise ReplayError(f"snapshot {index}: {exc}") from exc
                    raise _InvalidSnapshot(str(exc)) from exc
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

                evaluation = evaluate_snapshot(snapshot, self.config, state, features=features)
                state = evaluation.state
                decision = self._decision(
                    index=index,
                    snapshot=snapshot,
                    features=evaluation.features,
                    gates=evaluation.gates,
                    quality=evaluation.quality,
                    state=state,
                    signal=evaluation.signal,
                    state_updated=evaluation.state_updated,
                    risk=evaluation.risk,
                    sequence=sequence,
                )
                previous_close_time = close_time
            except (FeatureError, AsOfError, _InvalidSnapshot) as exc:
                sequence_counts[_failure_status(exc)] += 1
                decision = self._failure(index, snapshot, state, exc)
            except (KeyError, TypeError, ValueError) as exc:
                # Under fail_closed, a raw malformed snapshot is DATA_STALE, as live
                # treats it; ordering and identity errors (ReplayError) always stop.
                if self.invalid_snapshots == "abort" or isinstance(exc, ReplayError):
                    raise
                sequence_counts["SNAPSHOT_INVALID"] += 1
                decision = self._failure(index, snapshot, state, _InvalidSnapshot(str(exc)))

            signal_counts[decision["signal"]] += 1
            decisions.append(decision)

        baseline = {
            "replay_version": REPLAY_VERSION,
            "configuration": self.config.public_dict(),
            "configuration_id": stable_id("config", self.config.public_dict()),
            "invalid_snapshot_policy": self.invalid_snapshots,
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
        risk: dict[str, Any],
        sequence: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "replay_decision/0.2",
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
            "risk": risk,
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
            "schema_version": "replay_decision/0.2",
            "replay_version": REPLAY_VERSION,
            "snapshot_index": index,
            "observed_at_ms": snapshot.get("observed_at_ms"),
            "completed_candle_close_time_ms": None,
            "research_symbol": self.config.research_symbol,
            "executable_pair": self.config.executable_pair,
            "signal": "DATA_STALE",
            "action": "NO_ORDER",
            "execution_permission": "DENIED_HISTORICAL_REPLAY",
            "strategy_validated": False,
            "sequence": {
                "status": _failure_status(exc),
                "missing_bars": 0,
            },
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
            "risk": assess_decision_risk("DATA_STALE", state, None, self.config),
        }
        return {"decision_id": stable_id("replay_decision", payload), **payload}


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
    result.add_argument("--invalid-snapshots", choices=("abort", "fail_closed"), default="abort",
                        help="abort on a structurally invalid snapshot (default), or record it as "
                             "DATA_STALE and continue, as the live service does")
    result.add_argument("--output", required=True, help="immutable replay-result JSON path")
    result.add_argument("--fee-bps-per-side", type=float, default=None,
                        help="assumed exchange fee per side for the risk contract; unknown if omitted")
    result.add_argument("--slippage-bps-per-side", type=float, default=None,
                        help="assumed slippage per side for the risk contract; unknown if omitted")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = ObserverConfig(
        base_asset=args.base.upper(),
        mode="historical_replay",
        fee_bps_per_side=args.fee_bps_per_side,
        slippage_bps_per_side=args.slippage_bps_per_side,
    )
    try:
        config.validate()
    except ValueError as exc:
        parser().error(str(exc))
    strict = args.invalid_snapshots == "abort"
    snapshots = (
        load_jsonl_snapshots(args.jsonl, strict=strict)
        if args.jsonl
        else load_sqlite_snapshots(args.db, config.research_symbol, strict=strict)
    )
    result = ReplayRunner(config, invalid_snapshots=args.invalid_snapshots).run(snapshots)
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
