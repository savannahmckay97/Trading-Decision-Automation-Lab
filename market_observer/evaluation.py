"""Dataset-bound, descriptive decision evaluation; no fills or trading claims.

Recompute decisions rather than trusting an unrelated replay file. Compare the
existing setup with deliberately simple selection baselines on the same valid
completed bars. Future labels never enter the production evaluator.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .asof import AsOfError, validate_snapshot_asof
from .config import ObserverConfig
from .dataset import DatasetError, validate_dataset_manifest
from .outcomes import build_outcome_artifact, completed_candle_tape
from .replay import ReplayRunner, load_jsonl_snapshots
from .util import canonical_json, decimal, stable_id


EVALUATION_VERSION = "trading-decision-lab-evaluation-0.2.0"
POLICIES = ("TDAL", "POSITIVE_PRIOR_RETURN", "SELECT_ALL", "ABSTAIN_ALL")


class EvaluationError(ValueError):
    """An evaluation cannot be tied to trustworthy inputs or a stated window."""


@dataclass(frozen=True)
class EvaluationConfig:
    experiment_id: str
    partition: str
    start_observed_at_ms: int
    end_observed_at_ms: int
    horizon_bars: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (
            self.start_observed_at_ms, self.end_observed_at_ms, self.horizon_bars,
        )):
            raise EvaluationError("timestamps and horizon_bars must be integers")
        if not self.experiment_id.strip():
            raise EvaluationError("experiment_id is required")
        if self.partition not in {"exploratory", "holdout"}:
            raise EvaluationError("partition must be exploratory or holdout")
        if self.start_observed_at_ms < 0 or self.end_observed_at_ms < self.start_observed_at_ms:
            raise EvaluationError("invalid inclusive observation window")
        if self.horizon_bars < 1:
            raise EvaluationError("horizon_bars must be positive")


def build_evaluation_artifact(
    snapshots: Iterable[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    observer_config: ObserverConfig,
    config: EvaluationConfig,
) -> dict[str, Any]:
    """Evaluate selections, abstentions and coverage, not portfolio returns.

    Replay may use pre-window snapshots for causal state warmup. Evaluation
    includes only the first gate-passing observation of each completed candle
    in the stated observation window. Label paths ending beyond that window
    are censored. Missing outcomes are never scored as zero.
    """
    source = manifest.get("source")
    if not isinstance(source, str) or not source.strip():
        raise EvaluationError("manifest source is required")
    try:
        rows, rebuilt = validate_dataset_manifest(snapshots, manifest)
    except DatasetError as exc:
        raise EvaluationError(str(exc)) from exc
    for index, snapshot in enumerate(rows):
        _audit_asof(snapshot, index)

    replay = ReplayRunner(observer_config).run(rows)
    interval = observer_config.interval_ms
    outcomes = build_outcome_artifact(
        rows, replay, dataset_id=rebuilt["dataset_id"], interval_ms=interval,
        horizons_bars=(config.horizon_bars,),
    )
    labels = {label["decision_id"]: label for label in outcomes["labels"]}
    tape = completed_candle_tape(rows, interval)
    window_tape = completed_candle_tape(
        [row for row in rows if row["observed_at_ms"] <= config.end_observed_at_ms], interval,
    ) if any(row["observed_at_ms"] <= config.end_observed_at_ms for row in rows) else {}
    seen: set[int] = set()
    evaluated: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()
    for decision in replay["decisions"]:
        observed = decision["observed_at_ms"]
        if not config.start_observed_at_ms <= observed <= config.end_observed_at_ms:
            continue
        counts["observations_in_window"] += 1
        signal_counts[decision["signal"]] += 1
        if decision["signal"] in {"DATA_STALE", "RISK_BLOCKED"}:
            counts["blocked_observations"] += 1
            continue
        anchor = decision["completed_candle_close_time_ms"]
        if anchor in seen:
            counts["duplicate_valid_observations"] += 1
            continue
        seen.add(anchor)
        target = anchor + config.horizon_bars * interval
        label = labels[decision["decision_id"]]["horizons"][str(config.horizon_bars)]
        if target > config.end_observed_at_ms:
            label = {"status": "CENSORED", "reason": "horizon_exceeds_evaluation_window"}
        elif label["status"] == "AVAILABLE" and any(
            anchor + step * interval not in window_tape
            for step in range(1, config.horizon_bars + 1)
        ):
            label = {"status": "CENSORED", "reason": "path_not_observed_by_window_end"}
        features = decision["features"]
        selected = {
            "TDAL": decision["signal"] == "LONG_ELIGIBLE",
            "POSITIVE_PRIOR_RETURN": decimal(features["return_15m_pct"]) > 0,
            "SELECT_ALL": True,
            "ABSTAIN_ALL": False,
        }
        invalidation = None
        if selected["TDAL"]:
            setup = decision["setup"]
            level = decimal(setup["breakout_resistance"]) - (
                decimal(observer_config.failure_buffer_atr) * decimal(setup["breakout_atr"])
            )
            invalidation = {"reference_price": level, "close_breach": None, "wick_touch": None}
            if label["status"] == "AVAILABLE":
                path = [tape[anchor + step * interval] for step in range(1, config.horizon_bars + 1)]
                invalidation.update(
                    close_breach=any(bar["close"] < level for bar in path),
                    wick_touch=any(bar["low"] <= level for bar in path),
                )
        first_touch = None
        if selected["TDAL"]:
            path = None
            if label["status"] == "AVAILABLE":
                path = [tape[anchor + step * interval] for step in range(1, config.horizon_bars + 1)]
            first_touch = _first_touch(decision.get("risk"), label, path)
        evaluated.append({
            "decision_id": decision["decision_id"],
            "observed_at_ms": observed,
            "anchor_close_time_ms": anchor,
            "signal": decision["signal"],
            "selected": selected,
            "outcome": label,
            "invalidation": invalidation,
            "reward_first_touch": first_touch,
        })

    policies = {name: _policy_metrics(evaluated, name) for name in POLICIES}
    all_mean = policies["SELECT_ALL"]["mean_selected_forward_return_pct"]
    tdal_mean = policies["TDAL"]["mean_selected_forward_return_pct"]
    content = {
        "schema_version": "decision_evaluation/0.2",
        "evaluation_version": EVALUATION_VERSION,
        "dataset_id": rebuilt["dataset_id"],
        "jsonl_sha256": rebuilt["jsonl_sha256"],
        "replay_id": replay["replay_id"],
        "configuration_id": replay["baseline"]["configuration_id"],
        "observer_configuration": replay["baseline"]["configuration"],
        "evaluation_configuration": asdict(config),
        "coverage": {
            **{name: counts[name] for name in (
                "observations_in_window", "blocked_observations", "duplicate_valid_observations",
            )},
            "unique_valid_bars": len(evaluated),
            "outcome_status_counts": dict(sorted(Counter(row["outcome"]["status"] for row in evaluated).items())),
            "signal_observation_counts": dict(sorted(signal_counts.items())),
            "replay_gap_count": replay["summary"]["gap_count"],
        },
        "policies": policies,
        "tdal_selection_lift_vs_all_pct_points": (
            tdal_mean - all_mean if tdal_mean is not None and all_mean is not None else None
        ),
        "tdal_invalidation": {
            "labeled_eligible_count": sum(row["invalidation"] is not None and row["outcome"]["status"] == "AVAILABLE" for row in evaluated),
            "close_breach_count": sum(row["invalidation"] is not None and row["invalidation"]["close_breach"] is True for row in evaluated),
            "wick_touch_count": sum(row["invalidation"] is not None and row["invalidation"]["wick_touch"] is True for row in evaluated),
        },
        "tdal_reward_first_touch": _first_touch_summary(evaluated),
        "decisions": evaluated,
        "claims": {
            "descriptive_market_comparison_only": True,
            "performance_claim_available": False,
            "decision_quality_improvement_demonstrated": False,
            "probability_calibration_available": False,
            "untouched_holdout_verified": False,
            "execution_permission": "DENIED_RESEARCH_ONLY",
        },
        "limitations": [
            "Forward returns start at the completed candle close, not an obtainable entry",
            "No fees, fills, portfolio returns or risk-adjusted performance are estimated",
            "Negative avoided moves and positive missed moves are descriptive abstention proxies",
            "Overlapping horizons are dependent; no significance or confidence intervals are claimed",
            "Partition is declared by the caller; preregistration and untouched status require an external experiment record",
            "Timestamp checks cannot prove provider publication semantics, revisions or synchronized feature windows",
            "The score is uncalibrated and is not a probability",
            "Reward first-touch is gross price movement from the completed close; a target touch is not a fill",
            "First-touch summaries condition on decisions that had a reward reference",
        ],
    }
    return {"evaluation_id": stable_id("decision_evaluation", content), **content}


FIRST_TOUCH_STATUSES = (
    "TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_BAR", "NEITHER_WITHIN_HORIZON",
)


def _first_touch(risk: Any, label: dict[str, Any], path: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Which of reward reference and invalidation the future path reaches first.

    Two stop meanings are kept apart, as elsewhere. Wick rule: a bar whose low
    touches invalidation. If the same bar also reaches the target, the
    intrabar order is unknown and the result is AMBIGUOUS_SAME_BAR, never
    resolved in either direction. Close rule: invalidation happens only at a
    bar's close, so an intrabar target touch in that bar came first.
    """
    if not isinstance(risk, dict) or risk.get("status") != "AVAILABLE":
        return {"status": "RISK_UNAVAILABLE"}
    reward = risk.get("reward_reference") or {}
    if reward.get("status") != "AVAILABLE":
        return {"status": "TARGET_UNAVAILABLE", "reason": reward.get("reason")}
    if path is None:
        return {"status": label["status"], "reason": label.get("reason")}
    target = decimal(reward["level"])
    stop = decimal(risk["invalidation"]["level"])
    ratio = decimal(reward["gross_opportunity_to_risk"])
    wick = close = None
    for step, bar in enumerate(path, start=1):
        hit = bar["high"] >= target
        if wick is None and (hit or bar["low"] <= stop):
            wick = ("AMBIGUOUS_SAME_BAR" if hit and bar["low"] <= stop
                    else "TARGET_FIRST" if hit else "STOP_FIRST", step)
        if close is None and (hit or bar["close"] < stop):
            close = ("TARGET_FIRST" if hit else "STOP_FIRST", step)
        if wick is not None and close is not None:
            break
    neither = ("NEITHER_WITHIN_HORIZON", None)
    wick, close = wick or neither, close or neither
    return {
        "status": "LABELED",
        "target_level": target,
        "invalidation_level": stop,
        "gross_opportunity_to_risk": ratio,
        "wick_stop": {"result": wick[0], "bars": wick[1]},
        "close_stop": {"result": close[0], "bars": close[1]},
    }


def _first_touch_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    touches = [row["reward_first_touch"] for row in rows if row["reward_first_touch"] is not None]
    labeled = [item for item in touches if item["status"] == "LABELED"]
    summary: dict[str, Any] = {
        "selected_count": len(touches),
        "status_counts": dict(sorted(Counter(item["status"] for item in touches).items())),
    }
    for rule in ("wick_stop", "close_stop"):
        counts = Counter(item[rule]["result"] for item in labeled)
        decided = [item for item in labeled if item[rule]["result"] in {"TARGET_FIRST", "STOP_FIRST"}]
        targets = sum(item[rule]["result"] == "TARGET_FIRST" for item in decided)
        ratios = [decimal(item["gross_opportunity_to_risk"]) for item in decided]
        # Each decided case risks 1R and pays its own ratio if the target comes
        # first. At one common hit rate p the set breaks even when
        # p * sum(r) = (1 - p) * n, i.e. p = 1 / (1 + mean r). Averaging
        # 1 / (1 + r_i) instead overstates the threshold (Jensen; it is convex).
        realized = sum(
            (ratio if item[rule]["result"] == "TARGET_FIRST" else Decimal(-1))
            for item, ratio in zip(decided, ratios)
        )
        summary[rule] = {
            "result_counts": {name: counts[name] for name in FIRST_TOUCH_STATUSES},
            "decided_count": len(decided),
            "target_first_fraction_of_decided": Decimal(targets) / len(decided) if decided else None,
            "breakeven_target_rate_at_mean_ratio": (
                1 / (1 + sum(ratios, Decimal(0)) / len(ratios)) if decided else None
            ),
            "realized_gross_r_of_decided": realized if decided else None,
            "mean_realized_gross_r_of_decided": realized / len(decided) if decided else None,
        }
    summary["basis"] = "GROSS_DESCRIPTIVE_NO_COSTS_NOT_EXPECTANCY"
    return summary


def _audit_asof(snapshot: dict[str, Any], index: int) -> None:
    """Use the runtime availability contract; reject the whole research run."""
    try:
        validate_snapshot_asof(snapshot)
    except AsOfError as exc:
        raise EvaluationError(f"snapshot {index}: {exc}") from exc



def _policy_metrics(rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    selected = [row for row in rows if row["selected"][policy]]
    labeled = [row for row in selected if row["outcome"]["status"] == "AVAILABLE"]
    abstained = [row for row in rows if not row["selected"][policy] and row["outcome"]["status"] == "AVAILABLE"]
    returns = [decimal(row["outcome"]["return_pct"]) for row in labeled]
    adverse = [decimal(row["outcome"]["max_adverse_excursion_pct"]) for row in labeled]
    return {
        "selected_count": len(selected),
        "labeled_selected_count": len(labeled),
        "unavailable_or_censored_selected_count": len(selected) - len(labeled),
        "selection_fraction": Decimal(len(selected)) / len(rows) if rows else None,
        "mean_selected_forward_return_pct": sum(returns, Decimal(0)) / len(returns) if returns else None,
        "median_selected_forward_return_pct": median(returns) if returns else None,
        "worst_selected_forward_adverse_excursion_pct": min(adverse) if adverse else None,
        "positive_selected_outcome_count": sum(value > 0 for value in returns),
        "negative_selected_outcome_count": sum(value < 0 for value in returns),
        "flat_selected_outcome_count": sum(value == 0 for value in returns),
        "labeled_abstention_count": len(abstained),
        "avoided_negative_market_move_count": sum(decimal(row["outcome"]["return_pct"]) < 0 for row in abstained),
        "missed_positive_market_move_count": sum(decimal(row["outcome"]["return_pct"]) > 0 for row in abstained),
    }


def write_evaluation_artifact(result: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(result) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TDAL descriptive decision evaluation")
    parser.add_argument("--jsonl", required=True)
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--partition", required=True, choices=("exploratory", "holdout"))
    parser.add_argument("--start-observed-at-ms", required=True, type=int)
    parser.add_argument("--end-observed-at-ms", required=True, type=int)
    parser.add_argument("--horizon-bars", required=True, type=int)
    parser.add_argument("--base", default="TAO")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.dataset_manifest).read_text(encoding="utf-8"))
    result = build_evaluation_artifact(
        load_jsonl_snapshots(args.jsonl), manifest,
        observer_config=ObserverConfig(base_asset=args.base.upper()),
        config=EvaluationConfig(
            args.experiment_id, args.partition, args.start_observed_at_ms,
            args.end_observed_at_ms, args.horizon_bars,
        ),
    )
    write_evaluation_artifact(result, args.output)
    print(canonical_json({"evaluation_id": result["evaluation_id"], "coverage": result["coverage"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

