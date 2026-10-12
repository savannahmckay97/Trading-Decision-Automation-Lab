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
from .replay import ReplayError, bind_replay_to_snapshots, load_jsonl_snapshots
from .risk import maximum_quantity
from .util import canonical_json, decimal, stable_id


GOVERNOR_VERSION = "trading-decision-lab-risk-governor-0.2.0"
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


def _finite(value: Any) -> Decimal | None:
    try:
        result = decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def assess_trade(
    decision: dict[str, Any],
    account: PaperAccount,
    config: GovernorConfig,
    round_trip_cost_bps: Any | None = None,
    *,
    fall_back_to_decision_costs: bool = True,
) -> dict[str, Any]:
    """Approve a sized paper trade or block it, reporting every failed check.

    ``round_trip_cost_bps`` overrides the decision's own modelled cost. Governed
    replay passes ``fall_back_to_decision_costs=False``: its costs come only from
    the paper configuration, so a missing input (such as the spread) blocks rather
    than silently using the decision's figure. Without a complete cost the
    proposal is blocked: an unknown cost is never zero.
    Malformed geometry, costs or account state block with an explicit reason;
    nothing malformed reaches sizing.
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
    equity = _finite(account.equity_usd)
    start_of_day = _finite(account.start_of_day_equity_usd)
    daily = _finite(account.daily_realized_pnl_usd)
    streak = account.consecutive_losses
    if (equity is None or equity <= 0 or start_of_day is None or start_of_day < 0 or daily is None
            or type(streak) is not int or streak < 0):
        return {**base, "status": "BLOCKED", "reasons": ["ACCOUNT_STATE_INVALID"]}
    if config.kill_switch:
        reasons.append("KILL_SWITCH_ENGAGED")
    if account.open_position is not None:
        reasons.append("POSITION_LIMIT_REACHED")
    if daily <= -start_of_day * config.max_daily_loss_fraction:
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if account.consecutive_losses >= config.max_consecutive_losses:
        reasons.append("CONSECUTIVE_LOSS_LIMIT_REACHED")

    if not isinstance(decision.get("features", {}), (dict, type(None))):
        reasons.append("FEATURES_MALFORMED")
    risk = decision.get("risk")
    levels: dict[str, Decimal] = {}
    if not isinstance(risk, dict) or risk.get("status") != "AVAILABLE":
        reasons.append("RISK_CONTRACT_UNAVAILABLE")
    else:
        try:
            raw = {
                "reference": risk["reference_price"]["value"],
                "invalidation": risk["invalidation"]["level"],
                "atr": risk["invalidation"]["derivation"]["breakout_atr"],
            }
        except (KeyError, TypeError):
            raw = None
        parsed = {key: _finite(value) for key, value in (raw or {}).items()}
        if raw is None or any(value is None for value in parsed.values()):
            reasons.append("RISK_CONTRACT_MALFORMED")
        else:
            levels = parsed
    if levels:
        levels["emergency_stop"] = levels["invalidation"] - config.emergency_stop_atr * levels["atr"]
        # A long needs 0 < emergency stop <= invalidation < reference, from a positive ATR.
        if not (levels["atr"] > 0 and 0 < levels["emergency_stop"] <= levels["invalidation"] < levels["reference"]):
            reasons.append("INVALID_STOP_GEOMETRY")

    if round_trip_cost_bps is None and fall_back_to_decision_costs and isinstance(risk, dict):
        costs = risk.get("costs")
        round_trip_cost_bps = costs.get("modelled_round_trip_bps") if isinstance(costs, dict) else None
    if round_trip_cost_bps is None:
        reasons.append("COSTS_INCOMPLETE")
    else:
        round_trip_cost_bps = _finite(round_trip_cost_bps)
        if round_trip_cost_bps is None or round_trip_cost_bps < 0:
            reasons.append("COSTS_INVALID")

    if reasons:
        return {**base, "status": "BLOCKED", "reasons": reasons}

    sizing = maximum_quantity(
        equity_usd=equity,
        risk_fraction=config.risk_fraction,
        entry_price=levels["reference"],
        invalidation_price=levels["emergency_stop"],
        round_trip_cost_bps=round_trip_cost_bps,
        max_notional_usd=equity * config.max_notional_fraction,
    )
    return {
        **base,
        "status": "APPROVED_FOR_PAPER",
        "reasons": [],
        # Sized at the reference close. The simulator commits a quantity at the
        # modelled fill, which may be smaller (cash and risk are rechecked there).
        "quantity": sizing["maximum_quantity"],
        "risk_budget_usd": sizing["risk_budget_usd"],
        "sizing_reference_price": levels["reference"],
        "notional_cap_binding": sizing["maximum_quantity"] * levels["reference"]
        >= equity * config.max_notional_fraction,
        "exits": {
            "setup_invalidation": {"level": levels["invalidation"], "rule": "COMPLETED_CLOSE_BELOW"},
            "emergency_stop": {
                "level": levels["emergency_stop"],
                "rule": "INTRABAR_LOW_AT_OR_BELOW",
                "distance_below_invalidation_atr": config.emergency_stop_atr,
            },
            "time_exit": "MAX_HOLD_CLOSE",
            **({} if config.execution_model == "MARKET_BASELINE" else {
                # Levels are set from the committed maker entry, not the reference;
                # the simulator reports the actual levels used.
                "take_profit_limits": [
                    {"r_multiple": r, "fraction": f, "basis": "COMMITTED_ENTRY_MINUS_EMERGENCY_STOP",
                     "liquidity": "MAKER"}
                    for r, f in config.take_profit_tranches],
                "trailing_stop": {"trail_atr": config.trail_atr, "floor": levels["emergency_stop"],
                                  "rule": "HIGHEST_COMPLETED_HIGH_MINUS_TRAIL_ATR_RATCHET_UP",
                                  "entry_bar_rule": "EMERGENCY_FLOOR_ONLY",
                                  "liquidity": "TAKER"},
            }),
        },
        "round_trip_cost_bps": round_trip_cost_bps,
        "gap_loss_bounded": False,
    }


def _proposals(decisions: list[Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Eligible proposals in the order given, first observation per candle.

    Observation records (including DATA_STALE failures, which deliberately carry
    no candle anchor) are checked for chronological order by their own fields;
    only LONG_ELIGIBLE proposals need an anchor. Proposal anchors must not go
    backwards in the order supplied: contradictory chronology is rejected, never
    sorted away. Repeated polls of one eligible candle are one proposal; the
    duplicates are counted, not traded.
    """

    previous: tuple[int, int] | None = None
    last_anchor: int | None = None
    proposals: list[dict[str, Any]] = []
    seen: set[int] = set()
    counts = {"observations": 0, "non_proposals": 0, "duplicate_polls": 0}
    for item in decisions:
        if not isinstance(item, dict):
            raise GovernorError("every decision must be an object")
        counts["observations"] += 1
        order = (item.get("observed_at_ms"), item.get("snapshot_index"))
        if all(type(value) is int for value in order):
            if previous is not None and order < previous:
                raise GovernorError("replay decisions are not in observation order")
            previous = order
        if item.get("signal") != "LONG_ELIGIBLE":
            counts["non_proposals"] += 1
            continue
        anchor = item.get("completed_candle_close_time_ms")
        if type(anchor) is not int:
            raise GovernorError("a LONG_ELIGIBLE decision needs an integer completed_candle_close_time_ms")
        if last_anchor is not None and anchor < last_anchor:
            raise GovernorError("LONG_ELIGIBLE decisions go backwards in candle time; chronology is contradictory")
        last_anchor = anchor
        if anchor in seen:
            counts["duplicate_polls"] += 1
            continue
        seen.add(anchor)
        proposals.append(item)
    return proposals, counts


def build_governed_paper_artifact(
    snapshots: Iterable[dict[str, Any]],
    replay_result: dict[str, Any],
    *,
    dataset_id: str,
    paper_config: PaperExecutionConfig,
    config: GovernorConfig,
) -> dict[str, Any]:
    """Run every replay proposal through the governor on one paper account.

    An approved proposal reserves the single position slot from submission: a
    resting order until it fills, expires or is cancelled, and a position until it
    exits. So a later proposal is judged on what was known at its own time, never
    on how an earlier order eventually resolves.
    """

    if not dataset_id:
        raise GovernorError("dataset_id is required")
    if config.execution_model == "MAKER_STAGGERED" and config.maker_fee_bps is None:
        raise GovernorError("maker_fee_bps must be supplied for MAKER_STAGGERED; unknown is never zero")
    replay_id = replay_result.get("replay_id")
    decisions = replay_result.get("decisions")
    if not replay_id or not isinstance(decisions, list):
        raise GovernorError("replay_result must contain replay_id and decisions")
    ordered, observation_counts = _proposals(decisions)

    tape = completed_candle_tape(snapshots, paper_config.interval_ms)
    account = PaperAccount(equity_usd=config.starting_equity_usd, peak_equity_usd=config.starting_equity_usd)
    journal: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    max_drawdown = Decimal("0")
    censored_at: str | None = None

    def book(event: dict[str, Any]) -> None:
        nonlocal max_drawdown
        account.roll_to(utc_day(event["time_ms"]))
        account.equity_usd += event["pnl_usd"]
        account.daily_realized_pnl_usd += event["pnl_usd"]
        account.peak_equity_usd = max(account.peak_equity_usd, account.equity_usd)
        max_drawdown = max(max_drawdown, account.peak_equity_usd - account.equity_usd)

    def settle_until(time_ms: int) -> None:
        """Book each leg on its own bar and UTC day; release the slot when the
        order or position has resolved by ``time_ms``."""
        reservation = account.open_position
        if reservation is None:
            return
        pending = reservation["_pending_events"]
        while pending and pending[0]["time_ms"] <= time_ms:
            book(pending.pop(0))
        if pending or reservation["_release_time_ms"] > time_ms:
            return
        if reservation.get("status") == "FILLED":
            account.consecutive_losses = account.consecutive_losses + 1 if reservation["net_pnl_usd"] < 0 else 0
        account.open_position = None

    for decision in ordered:
        anchor = decision["completed_candle_close_time_ms"]
        if censored_at is not None:
            journal.append({"decision_id": decision.get("decision_id"), "status": "NOT_EVALUATED",
                            "reasons": ["ACCOUNT_PATH_CENSORED"]})
            continue
        settle_until(anchor)
        account.roll_to(utc_day(anchor))
        cost_bps = _paper_round_trip_bps(decision, paper_config, config)
        verdict = assess_trade(decision, account, config, cost_bps, fall_back_to_decision_costs=False)
        if verdict["status"] == "APPROVED_FOR_PAPER":
            simulate = _simulate_position if config.execution_model == "MARKET_BASELINE" else _simulate_maker_staggered
            outcome = simulate(decision, verdict, tape, paper_config, config, account.equity_usd)
            verdict["paper_outcome"] = outcome["status"]
            if outcome["status"] == "CENSORED":
                # Book what is already known (entry fee, earlier legs); the open
                # remainder's final value is unknown, so the account stops here.
                for event in outcome.get("events", []):
                    book(event)
                if outcome.get("events"):
                    trades.append(outcome)
                censored_at = verdict["decision_id"]
            else:
                events = outcome.get("events", [])
                release = events[-1]["time_ms"] if events else outcome["resolved_time_ms"]
                account.open_position = {**outcome, "_pending_events": list(events), "_release_time_ms": release}
                if outcome["status"] == "FILLED":
                    trades.append(outcome)
        journal.append(verdict)
    if account.open_position is not None:
        settle_until(account.open_position["_release_time_ms"])
    for trade in trades:
        trade.pop("_pending_events", None)
        trade.pop("_release_time_ms", None)

    blocked = [row for row in journal if row["status"] == "BLOCKED"]
    reason_counts: dict[str, int] = {}
    for row in blocked:
        for reason in row["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    baseline = {
        "governor_version": GOVERNOR_VERSION,
        "dataset_id": dataset_id,
        "replay_id": str(replay_id),
        # Bound to the decisions actually consumed, not only caller-supplied IDs.
        "decision_content_id": stable_id("governed_inputs", decisions),
        "governor_configuration": config.public_dict(),
        "paper_configuration": paper_config.public_dict(),
    }
    return {
        "schema_version": "governed_paper_artifact/0.4",
        "governed_paper_id": stable_id("governed_paper", baseline),
        **baseline,
        "journal": journal,
        "trades": trades,
        "summary": {
            **{f"{key}_count": value for key, value in observation_counts.items()},
            "proposal_count": len(journal),
            "approved_count": sum(row["status"] == "APPROVED_FOR_PAPER" for row in journal),
            "blocked_count": len(blocked),
            "block_reason_counts": dict(sorted(reason_counts.items())),
            "filled_trade_count": sum(trade["status"] == "FILLED" for trade in trades),
            "censored_open_trade_count": sum(trade["status"] == "CENSORED" for trade in trades),
            "starting_equity_usd": config.starting_equity_usd,
            "ending_equity_usd": account.equity_usd,
            "ending_equity_basis": "REALIZED_ONLY_OPEN_REMAINDER_UNVALUED" if censored_at else "REALIZED",
            # Peak-to-trough of equity after each booked leg; open positions are not
            # marked to market, so intratrade drawdown is not included.
            "max_realized_drawdown_usd": max_drawdown,
            "censored_at_decision_id": censored_at,
            "performance_claim_available": False,
            "execution_permission": "NONE",
        },
        "limitations": [
            "Paper simulation on research-venue candles; not Kraken executions and not evidence of profitability",
            "Entry is the first candle opening at or after the decision's observation time (observed_at_ms); a decision without one enters on the candle after its signal candle",
            "Sizing uses the completed close; the simulator commits at most that quantity at the modelled fill, rechecking cash and risk",
            "MARKET_BASELINE has no take-profit; MAKER_STAGGERED fills are assumed when price trades strictly through a resting limit, ignoring queue position, partial fills and adverse selection, so maker savings are an upper bound",
            "A resting limit fills at its own price even if a bar opens below it (no price improvement), which overstates the loss when that bar also gaps through the stop",
            "Within a bar the order of events is unknown: a fill comes before a stop, and a stop before a target, the conservative order in each case",
            "Tranche sizes, R multiples, entry TTL and trail width are owner-chosen a priori defaults, not validated or fitted",
            "Gaps through the emergency stop fill at the worse bar open; loss is not bounded",
            "Funding, depth, latency and partial fills are not modelled",
            "Drawdown is on realised equity after each leg, not marked to market",
            "A missing bar while an order or position is open ends the account simulation: known legs are booked, the open remainder is unvalued, and later proposals are not evaluated",
        ],
    }


def _paper_round_trip_bps(
    decision: dict[str, Any], paper_config: PaperExecutionConfig, config: GovernorConfig
) -> Decimal | None:
    features = decision.get("features")
    if not isinstance(features, dict):
        return None
    spread = _finite(features.get("spread_bps"))
    if spread is None or spread < 0:
        return None
    if config.execution_model == "MARKET_BASELINE":
        return spread + 2 * (paper_config.fee_bps_per_side + paper_config.slippage_bps_per_side)
    if config.maker_fee_bps is None:
        return None
    # Sizing assumes the worst case, a stop-out: maker entry, then a taker exit
    # that crosses half the spread and slips. A profitable path costs less.
    return (config.maker_fee_bps + paper_config.fee_bps_per_side
            + paper_config.slippage_bps_per_side + spread / 2)


def _committed_quantity(
    verdict: dict[str, Any], entry_price: Decimal, entry_fee_bps: Decimal, stop: Decimal,
    equity_usd: Decimal, config: GovernorConfig,
) -> tuple[Decimal, list[str]]:
    """The quantity actually committed at the modelled fill.

    Never more than the reference-sized quantity; reduced so that notional plus
    entry fee fits the no-leverage cash cap, and so that the loss to the
    emergency stop plus round-trip costs, measured from the actual entry, stays
    within the risk budget.
    """

    reasons: list[str] = []
    quantity = verdict["quantity"]
    cash_cap = equity_usd * config.max_notional_fraction / (entry_price * (1 + entry_fee_bps / TEN_THOUSAND))
    if cash_cap < quantity:
        quantity, reasons = cash_cap, reasons + ["RESIZED_TO_CASH_AT_FILL"]
    per_unit_risk = entry_price - stop + entry_price * verdict["round_trip_cost_bps"] / TEN_THOUSAND
    risk_cap = verdict["risk_budget_usd"] / per_unit_risk
    if risk_cap < quantity:
        quantity, reasons = risk_cap, reasons + ["RESIZED_TO_RISK_AT_FILL"]
    return quantity, reasons


def _path(decision: dict[str, Any], tape: dict[int, dict[str, Any]], paper_config: PaperExecutionConfig):
    """(pre-entry bars, bar getter from the entry candle on, entry step).

    The entry candle is the first one opening at or after the decision's
    observation time, so no price from before the decision exists is used. A
    decision without ``observed_at_ms`` (hand-built input) enters on the candle
    after its signal candle. Returns None as the first item if a pre-entry candle
    is missing.
    """
    anchor = decision["completed_candle_close_time_ms"]
    observed = decision.get("observed_at_ms")
    first_open = anchor + 1
    step0 = 1
    if type(observed) is int and observed > first_open:
        step0 = 1 + -(-(observed - first_open) // paper_config.interval_ms)  # ceiling division
    pre = []
    for step in range(1, step0):
        bar = tape.get(anchor + step * paper_config.interval_ms)
        if bar is None:
            return None, None, step0
        pre.append(bar)

    def get(offset: int):  # offset 0 = entry candle
        return tape.get(anchor + (step0 + offset) * paper_config.interval_ms)
    return pre, get, step0


def _not_entered_before(pre: list[dict[str, Any]], invalidation: Decimal) -> dict[str, Any] | None:
    for bar in pre:
        if bar["close"] < invalidation:
            return bar
    return None


def _simulate_position(
    decision: dict[str, Any],
    verdict: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    paper_config: PaperExecutionConfig,
    config: GovernorConfig,
    equity_usd: Decimal,
) -> dict[str, Any]:
    """Enter at the entry candle's open; exit on the emergency stop, a close below
    invalidation, or the max-hold close, in that order within each bar.

    Bars are consumed one at a time until the exit: a missing bar censors the
    trade only while the position is still open, and known legs are kept.
    """

    invalidation = verdict["exits"]["setup_invalidation"]["level"]
    stop = verdict["exits"]["emergency_stop"]["level"]
    pre, get, _ = _path(decision, tape, paper_config)
    if pre is None:
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    failed = _not_entered_before(pre, invalidation)
    if failed is not None:
        return {"status": "NOT_ENTERED_SETUP_INVALIDATED", "decision_id": verdict["decision_id"],
                "resolved_time_ms": failed["close_time_ms"]}
    first = get(0)
    if first is None:
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    raw_entry = first["open"]
    if raw_entry <= invalidation:
        return {"status": "NOT_ENTERED_OPEN_AT_OR_BELOW_INVALIDATION", "decision_id": verdict["decision_id"],
                "resolved_time_ms": first["open_time_ms"]}

    friction = decimal(decision["features"]["spread_bps"]) / 2 + paper_config.slippage_bps_per_side
    entry = _buy_price(raw_entry, friction)
    fee_rate = paper_config.fee_bps_per_side / TEN_THOUSAND
    quantity, resized = _committed_quantity(verdict, entry, paper_config.fee_bps_per_side, stop, equity_usd, config)
    entry_fee = quantity * entry * fee_rate
    entry_event = {"time_ms": first["close_time_ms"], "kind": "ENTRY_FEE", "pnl_usd": -entry_fee}
    base = {"decision_id": verdict["decision_id"], "reference_quantity": verdict["quantity"], "quantity": quantity,
            "resize_reasons": resized,
            "entry": {"raw_price": raw_entry, "effective_price": entry, "bar_close_time_ms": first["close_time_ms"]}}

    reason = exit_bar = raw_exit = None
    for offset in range(paper_config.max_hold_bars):
        bar = first if offset == 0 else get(offset)
        if bar is None:
            return {**base, "status": "CENSORED", "events": [entry_event], "open_quantity": quantity}
        if bar["low"] <= stop:
            reason, exit_bar, raw_exit = "EMERGENCY_STOP", bar, min(bar["open"], stop)
            break
        if bar["close"] < invalidation:
            reason, exit_bar, raw_exit = "SETUP_INVALIDATED_ON_CLOSE", bar, bar["close"]
            break
        if offset == paper_config.max_hold_bars - 1:
            reason, exit_bar, raw_exit = "MAX_HOLD", bar, bar["close"]

    exit_ = _sell_price(raw_exit, friction)
    exit_pnl = quantity * (exit_ - entry) - quantity * exit_ * fee_rate
    events = [entry_event, {"time_ms": exit_bar["close_time_ms"], "kind": reason, "pnl_usd": exit_pnl}]
    net_pnl = exit_pnl - entry_fee
    return {
        **base,
        "status": "FILLED",
        "exit": {"reason": reason, "raw_price": raw_exit, "effective_price": exit_,
                 "bar_close_time_ms": exit_bar["close_time_ms"]},
        "events": events,
        "net_pnl_usd": net_pnl,
        "realized_r": net_pnl / verdict["risk_budget_usd"],
    }


def _simulate_maker_staggered(
    decision: dict[str, Any],
    verdict: dict[str, Any],
    tape: dict[int, dict[str, Any]],
    paper_config: PaperExecutionConfig,
    config: GovernorConfig,
    equity_usd: Decimal,
) -> dict[str, Any]:
    """Maker entry, staggered maker take-profits, taker trailing stop.

    Conservative fill rules, because the candle tape has no queue or tick data:
    a resting limit fills only when price trades strictly through it, at its own
    price (no price improvement). A fill is processed before anything that can
    only be known at the bar's close, so a close below invalidation after a fill
    exits the position rather than cancelling the entry. Within one bar a stop
    beats a target, and targets are not credited on the entry bar. The entry bar
    is protected by the emergency floor only; the trail ratchets from highs of
    completed bars after entry. Price R and targets use the committed entry.
    Stops, the close rule and the time exit are taker. The not-entered rules
    match MARKET_BASELINE.
    """

    invalidation = verdict["exits"]["setup_invalidation"]["level"]
    floor = verdict["exits"]["emergency_stop"]["level"]
    maker, taker = config.maker_fee_bps, paper_config.fee_bps_per_side
    taker_friction = decimal(decision["features"]["spread_bps"]) / 2 + paper_config.slippage_bps_per_side

    pre, get, _ = _path(decision, tape, paper_config)
    if pre is None:
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    failed = _not_entered_before(pre, invalidation)
    if failed is not None:
        return {"status": "NOT_ENTERED_SETUP_INVALIDATED", "decision_id": verdict["decision_id"],
                "resolved_time_ms": failed["close_time_ms"]}
    first = get(0)
    if first is None:
        return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
    if first["open"] <= invalidation:
        return {"status": "NOT_ENTERED_OPEN_AT_OR_BELOW_INVALIDATION", "decision_id": verdict["decision_id"],
                "resolved_time_ms": first["open_time_ms"]}
    # Post-only buy placed at the entry candle's open, never above the signal close.
    limit = min(verdict["sizing_reference_price"], first["open"])
    fill_offset = None
    last_seen = first
    for offset in range(min(config.entry_ttl_bars, paper_config.max_hold_bars)):
        bar = first if offset == 0 else get(offset)
        if bar is None:
            return {"status": "CENSORED", "decision_id": verdict["decision_id"]}
        last_seen = bar
        if bar["low"] < limit:
            fill_offset = offset
            break
        if bar["close"] < invalidation:
            return {"status": "NOT_FILLED_SETUP_INVALIDATED", "decision_id": verdict["decision_id"],
                    "resolved_time_ms": bar["close_time_ms"]}
    if fill_offset is None:
        return {"status": "NOT_FILLED_TTL_EXPIRED", "decision_id": verdict["decision_id"],
                "resolved_time_ms": last_seen["close_time_ms"]}

    quantity, resized = _committed_quantity(verdict, limit, maker, floor, equity_usd, config)
    trail = decimal(verdict["exits"]["trailing_stop"]["trail_atr"]) * decimal(
        decision["risk"]["invalidation"]["derivation"]["breakout_atr"])
    r_unit = limit - floor
    targets = [[limit + r * r_unit, quantity * f, r] for r, f in config.take_profit_tranches]
    legs: list[dict[str, Any]] = []
    remaining = quantity
    high_so_far: Decimal | None = None  # highs of completed bars after entry only
    fill_bar = first if fill_offset == 0 else get(fill_offset)
    entry_fee = quantity * limit * maker / TEN_THOUSAND
    entry_event = {"time_ms": fill_bar["close_time_ms"], "kind": "ENTRY_FEE", "pnl_usd": -entry_fee}
    base = {"decision_id": verdict["decision_id"], "reference_quantity": verdict["quantity"], "quantity": quantity,
            "resize_reasons": resized,
            "entry": {"raw_price": limit, "effective_price": limit, "liquidity": "MAKER",
                      "bar_close_time_ms": fill_bar["close_time_ms"], "fee_usd": entry_fee},
            "price_r_unit": r_unit, "take_profit_levels": [target[0] for target in targets]}

    def leg(reason, liquidity, raw, qty, bar):
        effective = raw if liquidity == "MAKER" else _sell_price(raw, taker_friction)
        fee = qty * effective * (maker if liquidity == "MAKER" else taker) / TEN_THOUSAND
        legs.append({"reason": reason, "liquidity": liquidity, "quantity": qty, "raw_price": raw,
                     "effective_price": effective, "fee_usd": fee, "bar_close_time_ms": bar["close_time_ms"],
                     "pnl_usd": qty * (effective - limit) - fee})

    def events():
        return [entry_event] + [{"time_ms": item["bar_close_time_ms"], "kind": item["reason"],
                                 "pnl_usd": item["pnl_usd"]} for item in legs]

    last_bar = fill_bar
    for offset in range(fill_offset, paper_config.max_hold_bars):
        bar = get(offset)
        if bar is None:
            return {**base, "status": "CENSORED", "legs": legs, "events": events(), "open_quantity": remaining}
        last_bar = bar
        level = floor if high_so_far is None else max(floor, high_so_far - trail)
        if bar["low"] <= level:
            reason = "TRAILING_STOP" if level > floor else "EMERGENCY_STOP"
            leg(reason, "TAKER", min(bar["open"], level), remaining, bar)
            remaining = Decimal(0)
            break
        if offset > fill_offset:
            for target in targets:
                if target[1] > 0 and bar["high"] > target[0]:
                    leg(f"TAKE_PROFIT_{target[2]}R", "MAKER", target[0], target[1], bar)
                    remaining -= target[1]
                    target[1] = Decimal(0)
        if bar["close"] < invalidation:
            leg("SETUP_INVALIDATED_ON_CLOSE", "TAKER", bar["close"], remaining, bar)
            remaining = Decimal(0)
            break
        high_so_far = bar["high"] if high_so_far is None else max(high_so_far, bar["high"])
    if remaining > 0:
        leg("MAX_HOLD", "TAKER", last_bar["close"], remaining, last_bar)

    all_events = events()
    net_pnl = sum((event["pnl_usd"] for event in all_events), Decimal(0))
    return {
        **base,
        "status": "FILLED",
        "legs": legs,
        "exit": {"reason": legs[-1]["reason"], "raw_price": legs[-1]["raw_price"],
                 "effective_price": legs[-1]["effective_price"], "bar_close_time_ms": legs[-1]["bar_close_time_ms"]},
        "events": all_events,
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
    replay_result = _read_json_object(args.replay, "replay result")
    try:
        bind_replay_to_snapshots(rows, replay_result)
    except ReplayError as exc:
        raise GovernorError(str(exc)) from exc
    result = build_governed_paper_artifact(
        rows,
        replay_result,
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
