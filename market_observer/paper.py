"""Deterministic research-only paper fills for TDAL historical evaluation.

This module converts LONG_ELIGIBLE replay decisions into conservative hypothetical
fills using future completed research-venue candles. It is not an order router,
does not authorize trades, and does not pretend Binance research candles are
historical Kraken executions.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .dataset import build_dataset_artifact
from .outcomes import completed_candle_tape
from .replay import load_jsonl_snapshots
from .util import canonical_json, decimal, stable_id


PAPER_FILL_VERSION = "trading-decision-lab-paper-fill-0.1.0"
TEN_THOUSAND = Decimal("10000")
ONE_HUNDRED = Decimal("100")


class PaperExecutionError(ValueError):
    """Raised when a paper-fill artifact cannot be constructed reliably."""


@dataclass(frozen=True)
class PaperExecutionConfig:
    fee_bps_per_side: Decimal
    slippage_bps_per_side: Decimal
    max_hold_bars: int
    interval_ms: int = 900_000

    def __post_init__(self) -> None:
        fee = decimal(self.fee_bps_per_side)
        slippage = decimal(self.slippage_bps_per_side)
        if fee < 0 or slippage < 0:
            raise ValueError("fee and slippage bps must be non-negative")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be at least 1")
        if self.interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        object.__setattr__(self, "fee_bps_per_side", fee)
        object.__setattr__(self, "slippage_bps_per_side", slippage)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_paper_fill_artifact(
    snapshots: Iterable[dict[str, Any]],
    replay_result: dict[str, Any],
    *,
    dataset_id: str,
    config: PaperExecutionConfig,
) -> dict[str, Any]:
    """Simulate one-unit research-only paper fills from LONG_ELIGIBLE signals."""

    if not dataset_id:
        raise PaperExecutionError("dataset_id is required")
    replay_id = replay_result.get("replay_id")
    decisions = replay_result.get("decisions")
    baseline = replay_result.get("baseline")
    if not replay_id or not isinstance(decisions, list) or not isinstance(baseline, dict):
        raise PaperExecutionError("replay_result must contain replay_id, baseline and decisions")

    replay_config = baseline.get("configuration")
    if not isinstance(replay_config, dict):
        raise PaperExecutionError("replay baseline is missing configuration")
    try:
        failure_buffer_atr = decimal(replay_config["failure_buffer_atr"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperExecutionError("replay configuration is missing failure_buffer_atr") from exc
    if failure_buffer_atr < 0:
        raise PaperExecutionError("failure_buffer_atr must be non-negative")

    tape = completed_candle_tape(snapshots, config.interval_ms)
    fills = [
        _simulate_decision(
            decision,
            tape,
            config=config,
            failure_buffer_atr=failure_buffer_atr,
        )
        for decision in decisions
        if decision.get("signal") == "LONG_ELIGIBLE"
    ]

    baseline_payload = {
        "paper_fill_version": PAPER_FILL_VERSION,
        "dataset_id": dataset_id,
        "replay_id": str(replay_id),
        "configuration": config.public_dict(),
        "eligible_decision_ids": [fill["decision_id"] for fill in fills],
    }
    return {
        "schema_version": "paper_fill_artifact/0.1",
        "paper_fill_id": stable_id("paper_fill", baseline_payload),
        **baseline_payload,
        "fills": fills,
        "summary": {
            "eligible_signal_count": len(fills),
            "filled_count": sum(fill["status"] == "FILLED" for fill in fills),
            "unavailable_count": sum(fill["status"] == "UNAVAILABLE" for fill in fills),
            "invalidated_before_entry_count": sum(
                fill["status"] == "INVALIDATED_BEFORE_ENTRY" for fill in fills
            ),
            "performance_claim_available": False,
            "portfolio_simulation_present": False,
            "risk_governor_present": False,
        },
        "execution_contract": {
            "venue_basis": "BINANCE_USDM_RESEARCH_CANDLES",
            "executable_pair_reference": "KRAKEN_SPOT",
            "executable_venue_validated": False,
            "entry_rule": "NEXT_COMPLETED_BAR_OPEN_AFTER_LONG_ELIGIBLE",
            "exit_rule": "STRATEGY_INVALIDATION_OR_MAX_HOLD_CLOSE",
            "spread_rule": "HALF_SIGNAL_KRAKEN_SPREAD_EACH_SIDE",
            "slippage_rule": "FIXED_BPS_EACH_SIDE",
            "fee_rule": "FIXED_BPS_ON_EFFECTIVE_NOTIONAL_EACH_SIDE",
            "funding_model": "NOT_AVAILABLE_WITH_CURRENT_EXECUTION_TAPE",
        },
        "limitations": [
            "Research candles are not historical Kraken execution prints",
            "Signal-time Kraken spread is reused as a round-trip spread proxy",
            "No order-book depth, latency, queue position, partial fills, or liquidity impact",
            "Funding is not charged because exact historical execution-tape funding notional is unavailable",
            "Each fill is one-unit and independent; no portfolio equity or overlapping-position model",
            "Paper fills are research artifacts and cannot authorize live orders",
        ],
    }


def _simulate_decision(
    decision: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    *,
    config: PaperExecutionConfig,
    failure_buffer_atr: Decimal,
) -> dict[str, Any]:
    decision_id = str(decision.get("decision_id") or "")
    if not decision_id:
        raise PaperExecutionError("LONG_ELIGIBLE decision is missing decision_id")

    features = decision.get("features")
    setup = decision.get("setup")
    anchor = decision.get("completed_candle_close_time_ms")
    if anchor is None or not isinstance(features, dict) or not isinstance(setup, dict):
        return _unavailable(decision_id, "decision_missing_features_or_setup")

    try:
        anchor_time = int(anchor)
        spread_bps = decimal(features["spread_bps"])
        resistance = decimal(setup["breakout_resistance"])
        breakout_atr = decimal(setup["breakout_atr"])
    except (KeyError, TypeError, ValueError) as exc:
        return _unavailable(decision_id, f"decision_missing_execution_input:{type(exc).__name__}")

    if spread_bps < 0 or resistance <= 0 or breakout_atr <= 0:
        return _unavailable(decision_id, "invalid_execution_input")

    stop_reference = resistance - failure_buffer_atr * breakout_atr
    if stop_reference <= 0:
        return _unavailable(decision_id, "non_positive_invalidation")

    required_times = [
        anchor_time + step * config.interval_ms
        for step in range(1, config.max_hold_bars + 1)
    ]
    missing = [timestamp for timestamp in required_times if timestamp not in tape]
    if missing:
        return {
            "schema_version": "paper_fill/0.1",
            "decision_id": decision_id,
            "status": "UNAVAILABLE",
            "reason": "incomplete_future_path",
            "missing_candle_count": len(missing),
            "first_missing_close_time_ms": missing[0],
        }

    path = [tape[timestamp] for timestamp in required_times]
    first = path[0]
    raw_entry = first["open"]
    if raw_entry <= stop_reference:
        return {
            "schema_version": "paper_fill/0.1",
            "decision_id": decision_id,
            "status": "INVALIDATED_BEFORE_ENTRY",
            "entry_close_time_ms": first["close_time_ms"],
            "raw_entry_open": raw_entry,
            "invalidation_price": stop_reference,
        }

    half_spread_bps = spread_bps / Decimal("2")
    entry_friction_bps = half_spread_bps + config.slippage_bps_per_side
    exit_friction_bps = half_spread_bps + config.slippage_bps_per_side
    effective_entry = _buy_price(raw_entry, entry_friction_bps)

    exit_reason = "MAX_HOLD"
    exit_bar = path[-1]
    raw_exit = exit_bar["close"]
    for bar in path:
        if bar["low"] <= stop_reference:
            exit_reason = "INVALIDATION"
            exit_bar = bar
            raw_exit = bar["open"] if bar["open"] < stop_reference else stop_reference
            break

    effective_exit = _sell_price(raw_exit, exit_friction_bps)
    entry_fee = effective_entry * config.fee_bps_per_side / TEN_THOUSAND
    exit_fee = effective_exit * config.fee_bps_per_side / TEN_THOUSAND
    gross_pnl = raw_exit - raw_entry
    execution_pnl = effective_exit - effective_entry
    net_pnl = execution_pnl - entry_fee - exit_fee

    payload = {
        "schema_version": "paper_fill/0.1",
        "decision_id": decision_id,
        "status": "FILLED",
        "side": "LONG",
        "quantity": Decimal("1"),
        "entry": {
            "basis": "next_bar_open",
            "raw_price": raw_entry,
            "effective_price": effective_entry,
            "bar_close_time_ms": first["close_time_ms"],
            "signal_spread_bps": spread_bps,
            "half_spread_bps": half_spread_bps,
            "slippage_bps": config.slippage_bps_per_side,
            "fee_bps": config.fee_bps_per_side,
            "fee_usd_per_unit": entry_fee,
        },
        "invalidation_price": stop_reference,
        "exit": {
            "reason": exit_reason,
            "raw_price": raw_exit,
            "effective_price": effective_exit,
            "bar_close_time_ms": exit_bar["close_time_ms"],
            "half_spread_bps": half_spread_bps,
            "slippage_bps": config.slippage_bps_per_side,
            "fee_bps": config.fee_bps_per_side,
            "fee_usd_per_unit": exit_fee,
        },
        "gross_pnl_usd_per_unit": gross_pnl,
        "execution_pnl_before_fees_usd_per_unit": execution_pnl,
        "net_pnl_usd_per_unit": net_pnl,
        "gross_return_pct": _pct(raw_exit, raw_entry),
        "net_return_pct_on_effective_entry": ONE_HUNDRED * net_pnl / effective_entry,
        "holding_bars": _holding_bars(first["close_time_ms"], exit_bar["close_time_ms"], config.interval_ms),
        "executable_venue_validated": False,
    }
    return {"fill_id": stable_id("paper_fill_row", payload), **payload}


def _unavailable(decision_id: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "paper_fill/0.1",
        "decision_id": decision_id,
        "status": "UNAVAILABLE",
        "reason": reason,
    }


def _buy_price(raw: Decimal, friction_bps: Decimal) -> Decimal:
    return raw * (Decimal("1") + friction_bps / TEN_THOUSAND)


def _sell_price(raw: Decimal, friction_bps: Decimal) -> Decimal:
    multiplier = Decimal("1") - friction_bps / TEN_THOUSAND
    if multiplier <= 0:
        raise PaperExecutionError("exit friction must be less than 10000 bps")
    return raw * multiplier


def _pct(value: Decimal, anchor: Decimal) -> Decimal:
    return ONE_HUNDRED * (value / anchor - Decimal("1"))


def _holding_bars(entry_close_ms: int, exit_close_ms: int, interval_ms: int) -> int:
    return (int(exit_close_ms) - int(entry_close_ms)) // interval_ms + 1


def write_paper_fill_artifact(result: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(result) + "\n")


def _read_json_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperExecutionError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PaperExecutionError(f"{label} must be a JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Trading Decision Automation Lab — cost-aware research paper fills"
    )
    result.add_argument("--jsonl", required=True, help="frozen normalized-snapshot JSON Lines input")
    result.add_argument("--dataset-manifest", required=True, help="matching dataset manifest JSON")
    result.add_argument("--replay", required=True, help="historical replay result JSON")
    result.add_argument("--output", required=True, help="new immutable paper-fill JSON path")
    result.add_argument("--fee-bps-per-side", required=True, type=str)
    result.add_argument("--slippage-bps-per-side", required=True, type=str)
    result.add_argument("--max-hold-bars", required=True, type=int)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest = _read_json_object(args.dataset_manifest, "dataset manifest")
    source = manifest.get("source")
    expected_dataset_id = manifest.get("dataset_id")
    if not source or not expected_dataset_id:
        raise PaperExecutionError("dataset manifest is missing source or dataset_id")

    snapshots = list(load_jsonl_snapshots(args.jsonl))
    rows, rebuilt_manifest = build_dataset_artifact(snapshots, source=str(source))
    if rebuilt_manifest["dataset_id"] != expected_dataset_id:
        raise PaperExecutionError("dataset manifest does not match snapshot JSONL")
    if rebuilt_manifest["jsonl_sha256"] != manifest.get("jsonl_sha256"):
        raise PaperExecutionError("dataset manifest hash does not match snapshot JSONL")

    replay_result = _read_json_object(args.replay, "replay result")
    config = PaperExecutionConfig(
        fee_bps_per_side=decimal(args.fee_bps_per_side),
        slippage_bps_per_side=decimal(args.slippage_bps_per_side),
        max_hold_bars=args.max_hold_bars,
    )
    result = build_paper_fill_artifact(
        rows,
        replay_result,
        dataset_id=str(expected_dataset_id),
        config=config,
    )
    write_paper_fill_artifact(result, args.output)
    print(
        json.dumps(
            {
                "paper_fill_id": result["paper_fill_id"],
                **result["summary"],
                "output": str(Path(args.output)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
