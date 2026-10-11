"""Paper-only RiskGovernor: account-aware sizing and breakers for TDAL proposals.

The governor turns a LONG_ELIGIBLE decision into either a sized paper trade or
a block with explicit reasons, given a simulated account. It never submits or
authorizes an order: every record states ``execution_permission: NONE`` and the
only thing it can approve is a paper simulation. See docs/RISK_GOVERNOR.md.

Parameters were chosen by the repository owner on 2026-10-11 before any
governed run was examined. They are not fitted to outcomes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .dataset import DatasetError, validate_dataset_manifest
from .outcomes import completed_candle_tape
from .paper import (
    PaperExecutionConfig,
    PaperExecutionError,
    _buy_price,
    _read_json_object,
    _sell_price,
)
from .replay import load_jsonl_snapshots
from .risk import maximum_quantity
from .util import canonical_json, decimal, stable_id


GOVERNOR_VERSION = "trading-decision-lab-risk-governor-0.1.0"
TEN_THOUSAND = Decimal("10000")


class GovernorError(ValueError):
    """Raised when a governed paper run cannot be constructed reliably."""


@dataclass(frozen=True)
class GovernorConfig:
    starting_equity_usd: Decimal = Decimal("1000")
    risk_fraction: Decimal = Decimal("0.005")
    max_daily_loss_fraction: Decimal = Decimal("0.02")
    max_consecutive_losses: int = 3
    emergency_stop_atr: Decimal = Decimal("0.5")
    max_open_positions: int = 1
    max_notional_fraction: Decimal = Decimal("1")
    kill_switch: bool = False
    # Execution model. MAKER_STAGGERED (owner decision 2026-10-11): post-only limit
    # entry, staggered limit take-profits, trailing stop for the remainder; only
    # stops, the close-rule exit and the time exit pay taker costs. MARKET_BASELINE
    # is the original next-open market entry with no profit target, kept for comparison.
    execution_model: str = "MAKER_STAGGERED"
    maker_fee_bps: Decimal | None = None  # unknown is never zero: required for MAKER_STAGGERED
    entry_ttl_bars: int = 2
    take_profit_tranches: tuple = ((Decimal("1"), Decimal("0.3")), (Decimal("2"), Decimal("0.3")))
    trail_atr: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        for name in ("starting_equity_usd", "risk_fraction", "max_daily_loss_fraction",
                     "emergency_stop_atr", "max_notional_fraction"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if self.starting_equity_usd <= 0:
            raise ValueError("starting_equity_usd must be positive")
        if not Decimal("0") < self.risk_fraction <= Decimal("0.01"):
            raise ValueError("risk_fraction must be positive and no greater than 1%")
        if not Decimal("0") < self.max_daily_loss_fraction < Decimal("1"):
            raise ValueError("max_daily_loss_fraction must be between zero and one")
        if self.max_consecutive_losses < 1 or self.max_open_positions != 1:
            raise ValueError("max_consecutive_losses must be >= 1; only one open position is supported")
        if self.emergency_stop_atr < 0:
            raise ValueError("emergency_stop_atr must be non-negative")
        if not Decimal("0") < self.max_notional_fraction <= Decimal("1"):
            raise ValueError("max_notional_fraction must be in (0, 1]: no leverage")
        if self.execution_model not in {"MAKER_STAGGERED", "MARKET_BASELINE"}:
            raise ValueError("execution_model must be MAKER_STAGGERED or MARKET_BASELINE")
        if self.maker_fee_bps is not None:
            object.__setattr__(self, "maker_fee_bps", decimal(self.maker_fee_bps))
            if self.maker_fee_bps < 0:
                raise ValueError("maker_fee_bps must be non-negative")
        tranches = tuple((decimal(r), decimal(f)) for r, f in self.take_profit_tranches)
        object.__setattr__(self, "take_profit_tranches", tranches)
        multiples = [r for r, _ in tranches]
        if any(r <= 0 for r in multiples) or multiples != sorted(set(multiples)):
            raise ValueError("take-profit R multiples must be positive and strictly increasing")
        if any(f <= 0 for _, f in tranches) or sum((f for _, f in tranches), Decimal(0)) >= 1:
            raise ValueError("tranche fractions must be positive and leave a trailing remainder")
        object.__setattr__(self, "trail_atr", decimal(self.trail_atr))
        if self.trail_atr <= 0 or self.entry_ttl_bars < 1:
            raise ValueError("trail_atr must be positive and entry_ttl_bars at least 1")

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperAccount:
    equity_usd: Decimal
    day: str | None = None
    start_of_day_equity_usd: Decimal = Decimal("0")
    daily_realized_pnl_usd: Decimal = Decimal("0")
    consecutive_losses: int = 0
    open_position: dict[str, Any] | None = None
    peak_equity_usd: Decimal = field(default=Decimal("0"))

    def roll_to(self, day: str) -> None:
        """Start a new UTC trading day: daily loss and loss streak reset."""
        if self.day is None or day > self.day:
            self.day = day
            self.start_of_day_equity_usd = self.equity_usd
            self.daily_realized_pnl_usd = Decimal("0")
            self.consecutive_losses = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "equity_usd": self.equity_usd,
            "utc_day": self.day,
            "start_of_day_equity_usd": self.start_of_day_equity_usd,
            "daily_realized_pnl_usd": self.daily_realized_pnl_usd,
            "consecutive_losses": self.consecutive_losses,
            "open_position_decision_id": (self.open_position or {}).get("decision_id"),
        }


def utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def assess_trade(
    decision: dict[str, Any],
    account: PaperAccount,
    config: GovernorConfig,
    round_trip_cost_bps: Any | None = None,
) -> dict[str, Any]:
    """Approve a sized paper trade or block it, reporting every failed check.

    ``round_trip_cost_bps`` overrides the decision's own modelled cost (used by
    governed replay, where costs come from the paper configuration). Without a
    complete cost the proposal is blocked: an unknown cost is never zero.
    """

    base = {
        "governor_version": GOVERNOR_VERSION,
        "decision_id": decision.get("decision_id"),
        "execution_permission": "NONE",
        "account_before": account.snapshot(),
    }
    if decision.get("signal") != "LONG_ELIGIBLE":
        return {**base, "status": "NOT_A_PROPOSAL", "reasons": [f"signal_{decision.get('signal')}"]}

    reasons: list[str] = []
    if config.kill_switch:
        reasons.append("KILL_SWITCH_ENGAGED")
    if account.open_position is not None:
        reasons.append("POSITION_LIMIT_REACHED")
    if account.daily_realized_pnl_usd <= -account.start_of_day_equity_usd * config.max_daily_loss_fraction:
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if account.consecutive_losses >= config.max_consecutive_losses:
        reasons.append("CONSECUTIVE_LOSS_LIMIT_REACHED")

    risk = decision.get("risk")
    levels: dict[str, Decimal] = {}
    if not isinstance(risk, dict) or risk.get("status") != "AVAILABLE":
        reasons.append("RISK_CONTRACT_UNAVAILABLE")
    else:
        try:
            levels["reference"] = decimal(risk["reference_price"]["value"])
            levels["invalidation"] = decimal(risk["invalidation"]["level"])
            levels["atr"] = decimal(risk["invalidation"]["derivation"]["breakout_atr"])
        except (KeyError, TypeError, ValueError):
            reasons.append("RISK_CONTRACT_MALFORMED")
            levels = {}
    if levels:
        levels["emergency_stop"] = levels["invalidation"] - config.emergency_stop_atr * levels["atr"]
        if levels["emergency_stop"] <= 0 or levels["reference"] <= levels["invalidation"]:
            reasons.append("INVALID_STOP_GEOMETRY")

    if round_trip_cost_bps is None and isinstance(risk, dict):
        round_trip_cost_bps = (risk.get("costs") or {}).get("modelled_round_trip_bps")
    if round_trip_cost_bps is None:
        reasons.append("COSTS_INCOMPLETE")

    if reasons:
        return {**base, "status": "BLOCKED", "reasons": reasons}

    sizing = maximum_quantity(
        equity_usd=account.equity_usd,
        risk_fraction=config.risk_fraction,
        entry_price=levels["reference"],
        invalidation_price=levels["emergency_stop"],
        round_trip_cost_bps=round_trip_cost_bps,
        max_notional_usd=account.equity_usd * config.max_notional_fraction,
    )
    return {
        **base,
        "status": "APPROVED_FOR_PAPER",
        "reasons": [],
        "quantity": sizing["maximum_quantity"],
        "risk_budget_usd": sizing["risk_budget_usd"],
        "sizing_reference_price": levels["reference"],
        "notional_cap_binding": sizing["maximum_quantity"] * levels["reference"]
        >= account.equity_usd * config.max_notional_fraction,
        "exits": {
            "setup_invalidation": {"level": levels["invalidation"], "rule": "COMPLETED_CLOSE_BELOW"},
            "emergency_stop": {
                "level": levels["emergency_stop"],
                "rule": "INTRABAR_LOW_AT_OR_BELOW",
                "distance_below_invalidation_atr": config.emergency_stop_atr,
            },
            "time_exit": "MAX_HOLD_CLOSE",
            **({} if config.execution_model == "MARKET_BASELINE" else {
                "take_profit_limits": [
                    {"r_multiple": r, "fraction": f, "level": levels["reference"] + r * (levels["reference"] - levels["emergency_stop"]),
                     "liquidity": "MAKER"}
                    for r, f in config.take_profit_tranches],
                "trailing_stop": {"trail_atr": config.trail_atr, "floor": levels["emergency_stop"],
                                  "rule": "HIGHEST_COMPLETED_HIGH_MINUS_TRAIL_ATR_RATCHET_UP",
                                  "liquidity": "TAKER"},
            }),
        },
        "round_trip_cost_bps": decimal(round_trip_cost_bps),
        "gap_loss_bounded": False,
    }


def build_governed_paper_artifact(
    snapshots: Iterable[dict[str, Any]],
    replay_result: dict[str, Any],
    *,
    dataset_id: str,
    paper_config: PaperExecutionConfig,
    config: GovernorConfig,
) -> dict[str, Any]:
    """Run every replay decision through the governor on one paper account."""

    if not dataset_id:
        raise GovernorError("dataset_id is required")
    if config.execution_model == "MAKER_STAGGERED" and config.maker_fee_bps is None:
        raise GovernorError("maker_fee_bps must be supplied for MAKER_STAGGERED; unknown is never zero")
    replay_id = replay_result.get("replay_id")
    decisions = replay_result.get("decisions")
    if not replay_id or not isinstance(decisions, list):
        raise GovernorError("replay_result must contain replay_id and decisions")
    try:
        ordered = sorted(decisions, key=lambda item: int(item["completed_candle_close_time_ms"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise GovernorError("every decision needs completed_candle_close_time_ms") from exc

    tape = completed_candle_tape(snapshots, paper_config.interval_ms)
    account = PaperAccount(equity_usd=config.starting_equity_usd, peak_equity_usd=config.starting_equity_usd)
    journal: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    max_drawdown = Decimal("0")
    censored_at: str | None = None

    def settle_until(time_ms: int) -> None:
        nonlocal max_drawdown
        position = account.open_position
        if position is None or position["exit"]["bar_close_time_ms"] > time_ms:
            return
        account.roll_to(utc_day(position["exit"]["bar_close_time_ms"]))
        pnl = position["net_pnl_usd"]
        account.equity_usd += pnl
        account.daily_realized_pnl_usd += pnl
        account.consecutive_losses = account.consecutive_losses + 1 if pnl < 0 else 0
        account.peak_equity_usd = max(account.peak_equity_usd, account.equity_usd)
        max_drawdown = max(max_drawdown, account.peak_equity_usd - account.equity_usd)
        account.open_position = None

    for decision in ordered:
        anchor = int(decision["completed_candle_close_time_ms"])
        if censored_at is not None:
            journal.append({"decision_id": decision.get("decision_id"), "status": "NOT_EVALUATED",
                            "reasons": ["ACCOUNT_PATH_CENSORED"]})
            continue
        settle_until(anchor)
        account.roll_to(utc_day(anchor))
        cost_bps = _paper_round_trip_bps(decision, paper_config, config)
        verdict = assess_trade(decision, account, config, cost_bps)
        if verdict["status"] == "NOT_A_PROPOSAL":
            continue
        if verdict["status"] == "APPROVED_FOR_PAPER":
            simulate = _simulate_position if config.execution_model == "MARKET_BASELINE" else _simulate_maker_staggered
            outcome = simulate(decision, verdict, tape, paper_config, config)
            verdict["paper_outcome"] = outcome["status"]
            if outcome["status"] == "CENSORED":
                censored_at = verdict["decision_id"]
            elif outcome["status"] == "FILLED":
                account.open_position = outcome
                trades.append(outcome)
        journal.append(verdict)
    if account.open_position is not None:
        settle_until(account.open_position["exit"]["bar_close_time_ms"])

    blocked = [row for row in journal if row["status"] == "BLOCKED"]
    reason_counts: dict[str, int] = {}
    for row in blocked:
        for reason in row["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    baseline = {
        "governor_version": GOVERNOR_VERSION,
        "dataset_id": dataset_id,
        "replay_id": str(replay_id),
        "governor_configuration": config.public_dict(),
        "paper_configuration": paper_config.public_dict(),
    }
    return {
        "schema_version": "governed_paper_artifact/0.2",
        "governed_paper_id": stable_id("governed_paper", baseline),
        **baseline,
        "journal": journal,
        "trades": trades,
        "summary": {
            "proposal_count": len(journal),
            "approved_count": sum(row["status"] == "APPROVED_FOR_PAPER" for row in journal),
            "blocked_count": len(blocked),
            "block_reason_counts": dict(sorted(reason_counts.items())),
            "filled_trade_count": len(trades),
            "starting_equity_usd": config.starting_equity_usd,
            "ending_equity_usd": account.equity_usd,
            "max_drawdown_usd": max_drawdown,
            "censored_at_decision_id": censored_at,
            "performance_claim_available": False,
            "execution_permission": "NONE",
        },
        "limitations": [
            "Paper simulation on research-venue candles; not Kraken executions and not evidence of profitability",
            "Sizing uses the completed close; the fill is the next bar open, so realised risk can differ",
            "MARKET_BASELINE has no take-profit; MAKER_STAGGERED fills are assumed when price trades strictly through a resting limit, ignoring queue position, partial fills and adverse selection, so maker savings are an upper bound",
            "Tranche sizes, R multiples, entry TTL and trail width are owner-chosen a priori defaults, not validated or fitted",
            "Gaps through the emergency stop fill at the worse bar open; loss is not bounded",
            "Funding, depth, latency and partial fills are not modelled",
            "A censored future path ends the account simulation; later proposals are not evaluated",
        ],
    }


def _paper_round_trip_bps(
    decision: dict[str, Any], paper_config: PaperExecutionConfig, config: GovernorConfig
) -> Decimal | None:
    try:
        spread = decimal((decision.get("features") or {})["spread_bps"])
    except (KeyError, TypeError, ValueError):
        return None
    if spread < 0:
        return None
    if config.execution_model == "MARKET_BASELINE":
        return spread + 2 * (paper_config.fee_bps_per_side + paper_config.slippage_bps_per_side)
    if config.maker_fee_bps is None:
        return None
    # Sizing assumes the worst case, a stop-out: maker entry, then a taker exit
    # that crosses half the spread and slips. A profitable path costs less.
    return (config.maker_fee_bps + paper_config.fee_bps_per_side
            + paper_config.slippage_bps_per_side + spread / 2)


def _simulate_position(
    decision: dict[str, Any],
    verdict: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    paper_config: PaperExecutionConfig,
    config: GovernorConfig | None = None,
) -> dict[str, Any]:
    """Enter at the next bar open; exit on the emergency stop, a close below
    invalidation, or the max-hold close, in that order within each bar."""

    anchor = int(decision["completed_candle_close_time_ms"])
    times = [anchor + step * paper_config.interval_ms for step in range(1, paper_config.max_hold_bars + 1)]
    if any(timestamp not in tape for timestamp in times):
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    path = [tape[timestamp] for timestamp in times]
    invalidation = verdict["exits"]["setup_invalidation"]["level"]
    stop = verdict["exits"]["emergency_stop"]["level"]
    raw_entry = path[0]["open"]
    if raw_entry <= invalidation:
        return {"status": "NOT_ENTERED_OPEN_AT_OR_BELOW_INVALIDATION", "decision_id": verdict["decision_id"]}

    reason, exit_bar, raw_exit = "MAX_HOLD", path[-1], path[-1]["close"]
    for bar in path:
        if bar["low"] <= stop:
            reason, exit_bar = "EMERGENCY_STOP", bar
            raw_exit = min(bar["open"], stop)
            break
        if bar["close"] < invalidation:
            reason, exit_bar, raw_exit = "SETUP_INVALIDATED_ON_CLOSE", bar, bar["close"]
            break

    half_spread = decimal(decision["features"]["spread_bps"]) / 2
    friction = half_spread + paper_config.slippage_bps_per_side
    entry = _buy_price(raw_entry, friction)
    exit_ = _sell_price(raw_exit, friction)
    quantity = verdict["quantity"]
    fees = (entry + exit_) * paper_config.fee_bps_per_side / TEN_THOUSAND
    net_pnl = quantity * (exit_ - entry - fees)
    return {
        "status": "FILLED",
        "decision_id": verdict["decision_id"],
        "quantity": quantity,
        "entry": {"raw_price": raw_entry, "effective_price": entry, "bar_close_time_ms": path[0]["close_time_ms"]},
        "exit": {"reason": reason, "raw_price": raw_exit, "effective_price": exit_,
                 "bar_close_time_ms": exit_bar["close_time_ms"]},
        "net_pnl_usd": net_pnl,
        "realized_r": net_pnl / verdict["risk_budget_usd"],
    }


def _simulate_maker_staggered(
    decision: dict[str, Any],
    verdict: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    paper_config: PaperExecutionConfig,
    config: GovernorConfig,
) -> dict[str, Any]:
    """Maker entry, staggered maker take-profits, taker trailing stop.

    Conservative fill rules, because the candle tape has no queue or tick data:
    a resting limit fills only when price trades strictly through it, at its own
    price (no price improvement); within one bar a stop beats a target; targets
    are not credited on the entry bar; the stop level for a bar uses only highs
    completed before that bar. Stops, the close rule and the time exit are taker.
    """

    anchor = int(decision["completed_candle_close_time_ms"])
    times = [anchor + step * paper_config.interval_ms for step in range(1, paper_config.max_hold_bars + 1)]
    if any(timestamp not in tape for timestamp in times):
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    path = [tape[timestamp] for timestamp in times]
    invalidation = verdict["exits"]["setup_invalidation"]["level"]
    floor = verdict["exits"]["emergency_stop"]["level"]
    reference = verdict["sizing_reference_price"]
    r_unit = reference - floor
    quantity = verdict["quantity"]
    maker, taker = config.maker_fee_bps, paper_config.fee_bps_per_side
    taker_friction = decimal(decision["features"]["spread_bps"]) / 2 + paper_config.slippage_bps_per_side

    # Post-only buy placed at the first bar's open, never above the signal close.
    limit = min(reference, path[0]["open"])
    fill_index = None
    for index, bar in enumerate(path[: config.entry_ttl_bars]):
        if bar["close"] < invalidation:
            return {"status": "NOT_FILLED_SETUP_INVALIDATED", "decision_id": verdict["decision_id"]}
        if bar["low"] < limit:
            fill_index = index
            break
    if fill_index is None:
        return {"status": "NOT_FILLED_TTL_EXPIRED", "decision_id": verdict["decision_id"]}

    atr = decimal(verdict["exits"]["trailing_stop"]["trail_atr"]) * decimal(
        decision["risk"]["invalidation"]["derivation"]["breakout_atr"])
    targets = [[limit + r * r_unit, quantity * f, r] for r, f in config.take_profit_tranches]
    legs: list[dict[str, Any]] = []
    remaining = quantity
    high_so_far = path[fill_index]["high"]

    def leg(reason, liquidity, raw, qty, bar):
        effective = raw if liquidity == "MAKER" else _sell_price(raw, taker_friction)
        fee = qty * effective * (maker if liquidity == "MAKER" else taker) / TEN_THOUSAND
        legs.append({"reason": reason, "liquidity": liquidity, "quantity": qty, "raw_price": raw,
                     "effective_price": effective, "fee_usd": fee, "bar_close_time_ms": bar["close_time_ms"]})

    for index in range(fill_index, len(path)):
        bar = path[index]
        level = max(floor, high_so_far - atr)
        if bar["low"] <= level:
            reason = "TRAILING_STOP" if level > floor else "EMERGENCY_STOP"
            leg(reason, "TAKER", min(bar["open"], level), remaining, bar)
            remaining = Decimal(0)
            break
        if index > fill_index:
            for target in targets:
                if target[1] > 0 and bar["high"] > target[0]:
                    leg(f"TAKE_PROFIT_{target[2]}R", "MAKER", target[0], target[1], bar)
                    remaining -= target[1]
                    target[1] = Decimal(0)
            if bar["close"] < invalidation:
                leg("SETUP_INVALIDATED_ON_CLOSE", "TAKER", bar["close"], remaining, bar)
                remaining = Decimal(0)
                break
        high_so_far = max(high_so_far, bar["high"])
    if remaining > 0:
        leg("MAX_HOLD", "TAKER", path[-1]["close"], remaining, path[-1])

    entry_fee = quantity * limit * maker / TEN_THOUSAND
    net_pnl = sum((item["quantity"] * item["effective_price"] - item["fee_usd"] for item in legs),
                  Decimal(0)) - quantity * limit - entry_fee
    return {
        "status": "FILLED",
        "decision_id": verdict["decision_id"],
        "quantity": quantity,
        "entry": {"raw_price": limit, "effective_price": limit, "liquidity": "MAKER",
                  "bar_close_time_ms": path[fill_index]["close_time_ms"], "fee_usd": entry_fee},
        "legs": legs,
        "exit": {"reason": legs[-1]["reason"], "raw_price": legs[-1]["raw_price"],
                 "effective_price": legs[-1]["effective_price"], "bar_close_time_ms": legs[-1]["bar_close_time_ms"]},
        "net_pnl_usd": net_pnl,
        "realized_r": net_pnl / verdict["risk_budget_usd"],
        "maker_leg_share": Decimal(sum(1 for item in legs if item["liquidity"] == "MAKER")) / len(legs),
    }


def write_governed_paper_artifact(result: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(result) + "\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Trading Decision Automation Lab — paper-only RiskGovernor")
    result.add_argument("--jsonl", required=True, help="frozen normalized-snapshot JSON Lines input")
    result.add_argument("--dataset-manifest", required=True, help="matching dataset manifest JSON")
    result.add_argument("--replay", required=True, help="historical replay result JSON")
    result.add_argument("--output", required=True, help="new immutable governed-paper JSON path")
    result.add_argument("--fee-bps-per-side", required=True, type=str)
    result.add_argument("--slippage-bps-per-side", required=True, type=str)
    result.add_argument("--max-hold-bars", required=True, type=int)
    result.add_argument("--execution-model", choices=("MAKER_STAGGERED", "MARKET_BASELINE"),
                        default="MAKER_STAGGERED")
    result.add_argument("--maker-fee-bps", type=str, default=None,
                        help="assumed maker fee per side; required for MAKER_STAGGERED")
    result.add_argument("--kill-switch", action="store_true", help="block every new paper trade")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest = _read_json_object(args.dataset_manifest, "dataset manifest")
    dataset_id = manifest.get("dataset_id")
    if not manifest.get("source") or not dataset_id:
        raise GovernorError("dataset manifest is missing source or dataset_id")
    snapshots = list(load_jsonl_snapshots(args.jsonl))
    try:
        rows, _ = validate_dataset_manifest(snapshots, manifest)
    except DatasetError as exc:
        raise GovernorError(str(exc)) from exc
    try:
        paper_config = PaperExecutionConfig(
            fee_bps_per_side=decimal(args.fee_bps_per_side),
            slippage_bps_per_side=decimal(args.slippage_bps_per_side),
            max_hold_bars=args.max_hold_bars,
        )
    except (ValueError, PaperExecutionError) as exc:
        parser().error(str(exc))
    result = build_governed_paper_artifact(
        rows,
        _read_json_object(args.replay, "replay result"),
        dataset_id=str(dataset_id),
        paper_config=paper_config,
        config=GovernorConfig(kill_switch=args.kill_switch, execution_model=args.execution_model,
                              maker_fee_bps=args.maker_fee_bps),
    )
    write_governed_paper_artifact(result, args.output)
    print(json.dumps({"governed_paper_id": result["governed_paper_id"], **result["summary"],
                      "output": str(Path(args.output))}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
