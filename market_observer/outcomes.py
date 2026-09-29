"""Post-hoc market outcome labels for TDAL historical evaluation.

Labels are derived only from candles that were completed in a later historical
snapshot. They are descriptive market outcomes, not fills, trades, or strategy
performance, and must never be fed back into the decision evaluator.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .dataset import build_dataset_artifact
from .replay import load_jsonl_snapshots
from .util import canonical_json, decimal, stable_id


OUTCOME_LABEL_VERSION = "trading-decision-lab-outcomes-0.1.0"
DEFAULT_HORIZONS_BARS = (1, 4, 16, 96)


class OutcomeLabelError(ValueError):
    """Raised when future-market labels cannot be constructed reliably."""


def build_outcome_artifact(
    snapshots: Iterable[dict[str, Any]],
    replay_result: dict[str, Any],
    *,
    dataset_id: str,
    interval_ms: int = 900_000,
    horizons_bars: tuple[int, ...] = DEFAULT_HORIZONS_BARS,
) -> dict[str, Any]:
    """Build deterministic forward-market labels for replay decisions.

    A horizon is labeled only when every completed candle from the decision
    anchor through the requested future close is present. Missing paths remain
    explicitly unavailable instead of being interpolated.
    """

    if not dataset_id:
        raise OutcomeLabelError("dataset_id is required")
    if interval_ms <= 0:
        raise OutcomeLabelError("interval_ms must be positive")
    horizons = tuple(sorted(set(int(item) for item in horizons_bars)))
    if not horizons or any(item <= 0 for item in horizons):
        raise OutcomeLabelError("horizons_bars must contain positive integers")

    replay_id = replay_result.get("replay_id")
    decisions = replay_result.get("decisions")
    if not replay_id or not isinstance(decisions, list):
        raise OutcomeLabelError("replay_result must contain replay_id and decisions")

    tape = _completed_candle_tape(snapshots, interval_ms)
    labels = [
        _label_decision(decision, tape, interval_ms, horizons)
        for decision in decisions
    ]

    fully_labeled = sum(
        bool(label["horizons"])
        and all(value["status"] == "AVAILABLE" for value in label["horizons"].values())
        for label in labels
    )
    partially_labeled = sum(
        any(value["status"] == "AVAILABLE" for value in label["horizons"].values())
        and not all(value["status"] == "AVAILABLE" for value in label["horizons"].values())
        for label in labels
        if label["horizons"]
    )
    unlabelable = sum(not label["horizons"] for label in labels)

    baseline = {
        "outcome_label_version": OUTCOME_LABEL_VERSION,
        "dataset_id": dataset_id,
        "replay_id": str(replay_id),
        "interval_ms": interval_ms,
        "horizons_bars": list(horizons),
        "decision_ids": [label["decision_id"] for label in labels],
    }
    return {
        "schema_version": "historical_outcome_labels/0.1",
        "label_set_id": stable_id("outcome_labels", baseline),
        **baseline,
        "labels": labels,
        "summary": {
            "decision_count": len(labels),
            "fully_labeled_count": fully_labeled,
            "partially_labeled_count": partially_labeled,
            "unlabelable_count": unlabelable,
            "performance_claim_available": False,
            "fill_simulation_present": False,
        },
        "limitations": [
            "Forward labels describe market movement only",
            "No assumption that a signal produced an executable fill",
            "No fees, spread, slippage, funding, borrow, liquidation, or position sizing",
            "Outcome labels must not be used as evaluator inputs",
        ],
    }


def _completed_candle_tape(
    snapshots: Iterable[dict[str, Any]], interval_ms: int
) -> dict[int, dict[str, Any]]:
    tape: dict[int, dict[str, Any]] = {}
    previous_observed: int | None = None
    identity: tuple[str, str] | None = None

    for index, snapshot in enumerate(snapshots):
        try:
            observed = int(snapshot["observed_at_ms"])
            exchange_time = int(snapshot["exchange_time_ms"])
            current_identity = (
                str(snapshot["research_symbol"]),
                str(snapshot["executable_pair"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OutcomeLabelError(f"snapshot {index}: invalid identity or timestamp") from exc
        if previous_observed is not None and observed < previous_observed:
            raise OutcomeLabelError(f"snapshot {index}: observed_at_ms moved backwards")
        if exchange_time > observed:
            raise OutcomeLabelError(f"snapshot {index}: exchange_time_ms is after observed_at_ms")
        if identity is None:
            identity = current_identity
        elif current_identity != identity:
            raise OutcomeLabelError("snapshots mix instrument identities")
        previous_observed = observed

        for candle in snapshot.get("candles_15m", []):
            try:
                open_time = int(candle["open_time_ms"])
                close_time = int(candle["close_time_ms"])
                if close_time >= exchange_time:
                    continue
                normalized = {
                    "open_time_ms": open_time,
                    "close_time_ms": close_time,
                    "open": decimal(candle["open"]),
                    "high": decimal(candle["high"]),
                    "low": decimal(candle["low"]),
                    "close": decimal(candle["close"]),
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise OutcomeLabelError(f"snapshot {index}: malformed candle") from exc
            if close_time - open_time != interval_ms - 1:
                raise OutcomeLabelError(
                    f"snapshot {index}: candle interval does not match {interval_ms}ms"
                )
            if min(
                normalized["open"],
                normalized["high"],
                normalized["low"],
                normalized["close"],
            ) <= 0:
                raise OutcomeLabelError(f"snapshot {index}: non-positive OHLC value")
            if not (
                normalized["low"]
                <= min(normalized["open"], normalized["close"])
                <= max(normalized["open"], normalized["close"])
                <= normalized["high"]
            ):
                raise OutcomeLabelError(f"snapshot {index}: invalid OHLC ordering")
            existing = tape.get(close_time)
            if existing is not None and existing != normalized:
                raise OutcomeLabelError(
                    f"conflicting candle values for close_time_ms={close_time}"
                )
            tape[close_time] = normalized

    if not tape:
        raise OutcomeLabelError("no completed candles available for outcome labels")
    return tape


def _label_decision(
    decision: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    interval_ms: int,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    decision_id = decision.get("decision_id")
    if not decision_id:
        raise OutcomeLabelError("replay decision is missing decision_id")

    close_time = decision.get("completed_candle_close_time_ms")
    features = decision.get("features")
    if close_time is None or not isinstance(features, dict):
        return {
            "schema_version": "outcome_label/0.1",
            "decision_id": str(decision_id),
            "signal": decision.get("signal"),
            "anchor_close_time_ms": None,
            "anchor_price": None,
            "horizons": {},
            "label_status": "UNLABELABLE",
            "reason": "decision_has_no_completed_candle",
        }

    anchor_time = int(close_time)
    anchor_price = decimal(features["completed_close"])
    if anchor_price <= 0:
        raise OutcomeLabelError(f"decision {decision_id}: non-positive anchor price")

    outcomes: dict[str, dict[str, Any]] = {}
    for bars in horizons:
        required_times = [
            anchor_time + step * interval_ms for step in range(1, bars + 1)
        ]
        missing = [timestamp for timestamp in required_times if timestamp not in tape]
        key = str(bars)
        if missing:
            outcomes[key] = {
                "status": "UNAVAILABLE",
                "horizon_bars": bars,
                "target_close_time_ms": required_times[-1],
                "missing_candle_count": len(missing),
            }
            continue

        path = [tape[timestamp] for timestamp in required_times]
        final_close = path[-1]["close"]
        highest = max(candle["high"] for candle in path)
        lowest = min(candle["low"] for candle in path)
        outcomes[key] = {
            "status": "AVAILABLE",
            "horizon_bars": bars,
            "target_close_time_ms": required_times[-1],
            "end_close": final_close,
            "return_pct": _pct(final_close, anchor_price),
            "max_favorable_excursion_pct": _pct(highest, anchor_price),
            "max_adverse_excursion_pct": _pct(lowest, anchor_price),
        }

    available = sum(item["status"] == "AVAILABLE" for item in outcomes.values())
    label_status = (
        "FULL"
        if available == len(outcomes)
        else "PARTIAL"
        if available
        else "UNAVAILABLE"
    )
    return {
        "schema_version": "outcome_label/0.1",
        "decision_id": str(decision_id),
        "signal": decision.get("signal"),
        "anchor_close_time_ms": anchor_time,
        "anchor_price": anchor_price,
        "horizons": outcomes,
        "label_status": label_status,
    }


def _pct(value: Decimal, anchor: Decimal) -> Decimal:
    return Decimal("100") * (value / anchor - Decimal("1"))


def write_outcome_artifact(result: dict[str, Any], path: str | Path) -> None:
    """Write an immutable outcome-label artifact."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(result) + "\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Trading Decision Automation Lab — post-hoc outcome labels"
    )
    result.add_argument("--jsonl", required=True, help="frozen normalized-snapshot JSON Lines input")
    result.add_argument("--dataset-manifest", required=True, help="matching dataset manifest JSON")
    result.add_argument("--replay", required=True, help="historical replay result JSON")
    result.add_argument("--output", required=True, help="new immutable outcome-label JSON path")
    result.add_argument(
        "--horizons-bars",
        default="1,4,16,96",
        help="comma-separated completed-bar horizons; default 1,4,16,96",
    )
    return result


def _read_json_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeLabelError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise OutcomeLabelError(f"{label} must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest = _read_json_object(args.dataset_manifest, "dataset manifest")
    source = manifest.get("source")
    expected_dataset_id = manifest.get("dataset_id")
    if not source or not expected_dataset_id:
        raise OutcomeLabelError("dataset manifest is missing source or dataset_id")

    snapshots = list(load_jsonl_snapshots(args.jsonl))
    rows, rebuilt_manifest = build_dataset_artifact(snapshots, source=str(source))
    if rebuilt_manifest["dataset_id"] != expected_dataset_id:
        raise OutcomeLabelError("dataset manifest does not match snapshot JSONL")
    if rebuilt_manifest["jsonl_sha256"] != manifest.get("jsonl_sha256"):
        raise OutcomeLabelError("dataset manifest hash does not match snapshot JSONL")

    replay_result = _read_json_object(args.replay, "replay result")
    try:
        horizons = tuple(int(item.strip()) for item in args.horizons_bars.split(",") if item.strip())
    except ValueError as exc:
        raise OutcomeLabelError("horizons-bars must be comma-separated integers") from exc

    result = build_outcome_artifact(
        rows,
        replay_result,
        dataset_id=str(expected_dataset_id),
        horizons_bars=horizons,
    )
    write_outcome_artifact(result, args.output)
    print(
        json.dumps(
            {
                "label_set_id": result["label_set_id"],
                **result["summary"],
                "output": str(Path(args.output)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
