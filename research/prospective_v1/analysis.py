"""The one prospective analysis of BREAKOUT_RETEST_V1 on TAO (PREREGISTRATION.md §3–§5).

Run once, after the §5 trigger. Every rule below is the plan's. Where the plan was
silent or ambiguous, the choice is in ``RESOLVED_AMBIGUITIES`` (copied into every
result) and marked ``CHOICE`` at the code that applies it. Nothing here changes a frozen
v1 strategy parameter: the decisions come from the pinned 76dbb5d replay rows.

Order of work: verify every input (any failure halts and writes nothing), build the
population from decision fields only, then read prices. The result JSON and a short
summary (``<output stem>.summary.md``) are created exclusively, never overwritten.

Usage (PYTHONPATH = repository root)::

    python research/prospective_v1/analysis.py --export-dir OUT --output results/prospective_v1.json \
        [--population-manifest-sha256 SHA] [--plan research/prospective_v1/PREREGISTRATION.md]

``OUT`` is the directory written by ``prospective_data.py all`` (PR #26); ``inputs.py``
is the only module that knows its layout. The cut-off is derived (§5). Before the
trigger, a holdout run halts. ``--cutoff-ms`` after the trigger is recorded as a
deviation. ``--non-holdout-smoke [--window-start-ms MS] --cutoff-ms MS`` labels a run
on non-holdout data as such and accepts any replay version. Exit code 2 means HALT.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inputs import (  # noqa: E402
    PINNED_REPLAY_VERSION, ExportInputs, InputError, PooledRow, load_export, sha256_file, snapshot_id,
    stream_verified_snapshots,
)
from market_observer.config import ObserverConfig  # noqa: E402
from market_observer.evaluation import _first_touch, _first_touch_summary, _policy_metrics  # noqa: E402
from market_observer.governor import GovernorConfig, GovernorError, build_governed_paper_artifact, utc_day  # noqa: E402
from market_observer.outcomes import OutcomeLabelError, _label_decision  # noqa: E402
from market_observer.paper import PaperExecutionConfig, PaperExecutionError  # noqa: E402
from market_observer.reward_reference import assess_reward_reference  # noqa: E402
from market_observer.util import canonical_json, decimal, stable_id  # noqa: E402


ANALYSIS_VERSION = "prospective-v1-analysis-0.1.0"
INTERVAL_MS = 900_000
DAY_MS = 86_400_000
BLOCKED_SIGNALS = {"DATA_STALE", "RISK_BLOCKED"}


def utc_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Settings:
    """The plan's fixed values. Only a labelled non-holdout smoke run may move the window."""
    holdout_start_ms: int = utc_ms("2026-10-11T06:08:13")            # §2
    block_origin_ms: int = utc_ms("2026-10-11T00:00:00")             # §4, T0
    deadline_ms: int = utc_ms("2028-04-11T00:00:00")                 # §5 (amended 4a783f2)
    trigger_targeted_cases: int = 200                                # §5 (amended 4a783f2)
    primary_horizon: int = 16                                        # §4
    close_horizons_secondary: tuple[int, ...] = (4, 96)              # §4 secondary 3
    wick_horizons: tuple[int, ...] = (4, 16, 96)                     # §4 secondary 3
    forward_baseline_bars: int = 16                                  # §4 secondary 5
    comparator_d: Decimal = Decimal("0.6797")                        # §4 secondary 1
    seed: int = 20261012
    draws: int = 10_000
    block_lengths_days: tuple[int, ...] = (1, 2, 7)                  # 1 = UTC day (primary)
    sparse_min_entered: int = 30
    sparse_min_entered_days: int = 15
    comparator_min_entered: int = 30
    # §4 secondary 6: dated fee assumptions read 2026-10-13 (Kraken Pro spot).
    ledger_fee_tiers: tuple[tuple[str, Decimal, Decimal], ...] = (
        ("TIER_4_OWNER", Decimal("35"), Decimal("20")),
        ("TIER_1_SENSITIVITY", Decimal("80"), Decimal("40")),
    )
    ledger_slippage_bps: Decimal = Decimal("5")
    # CHOICE (ledger_max_hold): the plan fixes no max hold for the ledger; 16 bars is the
    # primary horizon and the value PR #11's own runs used.
    ledger_max_hold_bars: int = 16
    poll_gap_ms: int = 120_000  # recorder gap = no snapshot for longer than two 60 s polls
    partition: str = "HOLDOUT_CONFIRMATORY"
    expected_replay_version: str = PINNED_REPLAY_VERSION

    def public(self) -> dict[str, Any]:
        return json.loads(canonical_json(asdict(self)))


RESOLVED_AMBIGUITIES = {
    "cutoff": "§5 names the trigger but not the cut-off instant. The cut-off is the observed_at_ms "
              "of the first observation of the 200th targeted candle, or the deadline if earlier; "
              "an explicit --cutoff-ms is recorded as such and flagged if it differs.",
    "horizon_censoring": "A case whose horizon would end after the cut-off (close of entry candle "
                         "+ H - 1) is censored whatever happens earlier, so censoring never depends "
                         "on the path. Each horizon censors separately.",
    "pre_entry_missing": "Not-entered rules are evaluated in order on the candles present: a rule that "
                         "fires on a present candle settles the case (0R, flagged when a pre-entry "
                         "candle is missing); if no rule fires and a candle the rule needs is "
                         "missing, the case is censored.",
    "pre_entry_rule_order": "With several pre-entry candles, rule 1 is checked on all of them before "
                            "rule 2 (the plan's order), not candle by candle. Both score 0R.",
    "candle_conflicts": "A completed candle observed with different OHLC in two snapshots halts the "
                        "analysis (fail closed, as market_observer/outcomes.py does).",
    "candle_source": "Price candles come from snapshots of the segment that supplies that candle "
                     "(segment 1 before the export's effective cutover candle, segment 2 from it on, "
                     "as pooled), observed at or before the cut-off.",
    "entered_only_units": "The entered-only mean's bootstrap units are the blocks with at least one "
                          "entered trade (so no draw is empty).",
    "censoring_sensitivity_units": "Censoring sensitivities resample blocks with at least one case "
                                   "(scored or censored).",
    "bootstrap_draw": "Each draw picks n unit indices with rng.randrange(n), one after another; "
                      "intervals are the 250th and 9,750th sorted values (indices 249, 9749).",
    "contrast_sparse": "The contrast's sparse rule counts the TDAL and comparator cases in the "
                       "retained UTC-day blocks (blocks with both arms scored).",
    "contrast_reading": "The contrast is read on UTC-day units; 2- and 7-day blocks are sensitivities.",
    "comparator_invalid_levels": "A comparator candle whose ATR, invalidation or features are "
                                 "unusable is excluded and counted, like TARGET_UNAVAILABLE.",
    "eligible_risk_unavailable": "A LONG_ELIGIBLE first observation whose risk contract is not "
                                 "AVAILABLE is neither targeted nor TARGET_UNAVAILABLE; it is counted "
                                 "and excluded from both, and from the comparator.",
    "ledger_population": "The ledger receives every LONG_ELIGIBLE first gate-passing observation "
                         "(targeted and TARGET_UNAVAILABLE): the governor needs no reward level.",
    "ledger_entry_time": "Each decision handed to the governor carries the §3 decision time in "
                         "observed_at_ms, which the governor uses for entry timing.",
    "ledger_max_hold": "16 bars (see Settings.ledger_max_hold_bars).",
    "blocked_row_candle": "Pooling is the export's (population.jsonl). For uptime, a blocked row with "
                          "no completed candle counts for the candle complete at its observed_at_ms.",
    "coverage_definitions": "Expected candles: closes in [holdout start, cut-off). Uptime: share "
                            "with any pooled replay row; decision coverage: share with a first "
                            "gate-passing observation; recorder gaps: >120 s between snapshots.",
}


class AnalysisHalt(RuntimeError):
    """The analysis must stop until the cause is explained (§2, §3, §6)."""


# --- observations and population (decision fields only) ---------------------

Observation = PooledRow  # a pooled replay row (inputs.PooledRow), in pooling order


def implied_candle(item: Observation) -> int:
    """A blocked row without a candle counts for the candle it would have evaluated."""
    return item.candle if item.candle is not None else (item.observed_at_ms // INTERVAL_MS) * INTERVAL_MS - 1


def check_population_flags(rows: list[Observation]) -> None:
    """Recompute the §3 flags over the whole pooled series; any disagreement halts."""
    first: dict[int, Observation] = {}
    for item in rows:
        blocked = item.signal in BLOCKED_SIGNALS
        is_first = is_duplicate = differs = False
        if not blocked and item.candle is not None:
            if item.candle in first:
                is_duplicate, differs = True, item.signal != first[item.candle].signal
            else:
                first[item.candle], is_first = item, True
        expected = (blocked, is_first, is_duplicate, differs, is_first and classify(item) == "TARGETED")
        actual = (item.blocked, item.first_gate_passing, item.duplicate, item.signal_differs_from_first,
                  item.targeted)
        if expected != actual:
            raise AnalysisHalt(f"population row {item.pooled_index}: flags {actual} != recomputed {expected}")
        if is_first and item.row is None:
            raise AnalysisHalt(f"population row {item.pooled_index}: no replay row attached")


def first_observations(observations: Iterable[Observation], start_ms: int, cutoff_ms: int | None) -> dict[str, Any]:
    """First gate-passing observation per completed candle, in the window (§3)."""
    counts: Counter[str] = Counter()
    first: dict[int, Observation] = {}
    changed: list[dict[str, Any]] = []
    for item in observations:
        if not start_ms <= item.observed_at_ms <= (cutoff_ms if cutoff_ms is not None else item.observed_at_ms):
            continue
        counts["rows_in_window"] += 1
        if item.signal in BLOCKED_SIGNALS or item.candle is None:
            counts["blocked_rows"] += 1
            continue
        if item.candle in first:
            counts["duplicate_rows"] += 1
            if item.signal != first[item.candle].signal:
                changed.append({"candle_close_time_ms": item.candle, "first_signal": first[item.candle].signal,
                                "later_signal": item.signal, "segment": item.segment,
                                "snapshot_index": item.snapshot_index})
            continue
        first[item.candle] = item
    ordered = [first[key] for key in sorted(first)]
    return {"first": ordered, "counts": dict(counts), "changed_signal_duplicates": changed}


def classify(item: Observation) -> str:
    if item.signal != "LONG_ELIGIBLE":
        return "NOT_ELIGIBLE"
    if item.risk_status != "AVAILABLE":
        return "ELIGIBLE_RISK_UNAVAILABLE"
    return "TARGETED" if item.reward_reference_status == "AVAILABLE" else "TARGET_UNAVAILABLE"


def derive_cutoff(observations: list[Observation], settings: Settings) -> dict[str, Any]:
    """§5: the earlier of the 200th targeted case and the deadline (CHOICE: cutoff)."""
    selection = first_observations(observations, settings.holdout_start_ms, None)
    targeted = [item for item in selection["first"] if classify(item) == "TARGETED"]
    last_seen = max((item.observed_at_ms for item in observations), default=None)
    trigger = (targeted[settings.trigger_targeted_cases - 1].observed_at_ms
               if len(targeted) >= settings.trigger_targeted_cases else None)
    if trigger is not None and trigger <= settings.deadline_ms:
        return {"met": True, "by": "TARGETED_CASE_COUNT", "cutoff_ms": trigger}
    if last_seen is not None and last_seen >= settings.deadline_ms:
        return {"met": True, "by": "DEADLINE", "cutoff_ms": settings.deadline_ms}
    return {"met": False, "by": None, "cutoff_ms": None, "targeted_so_far": len(targeted),
            "last_observed_at_ms": last_seen}


# --- prices -----------------------------------------------------------------

CANDLE_FIELDS = ("open_time_ms", "close_time_ms", "open", "high", "low", "close")


def add_snapshot_candles(tape: dict[int, dict[str, Any]], snapshot: dict[str, Any], segment: str,
                         supplier: Callable[[int], str | None]) -> None:
    """Completed candles only (close before exchange time), from the supplying segment."""
    exchange = int(snapshot["exchange_time_ms"])
    for candle in snapshot.get("candles_15m", []):
        close = int(candle["close_time_ms"])
        if close >= exchange or supplier(close) != segment:
            continue
        normalized = {key: candle[key] for key in CANDLE_FIELDS}
        if min(normalized["open"], normalized["high"], normalized["low"], normalized["close"]) <= 0:
            raise AnalysisHalt(f"non-positive price in candle {close}")
        existing = tape.get(close)
        if existing is not None and existing != normalized:
            raise AnalysisHalt(f"conflicting candle values for close_time_ms={close} "
                               f"(segment {segment}); CHOICE candle_conflicts")
        tape[close] = normalized


# --- trade rules (§4) -------------------------------------------------------

def entry_offset(signal_close_ms: int, decision_time_ms: int) -> int:
    """k such that candle k after the signal candle is the first opening at/after the decision."""
    first_open = signal_close_ms + 1
    if decision_time_ms <= first_open:
        return 1
    return 1 + -(-(decision_time_ms - first_open) // INTERVAL_MS)


def simulate_trade(*, signal_close_ms: int, decision_time_ms: int, invalidation: Decimal,
                   target: Decimal | None, risk_distance: Decimal, tape: dict[int, dict[str, Any]],
                   horizon: int, stop_rule: str, cutoff_ms: int) -> dict[str, Any]:
    """One hypothetical gross long trade (§4 primary; secondaries 1–3 vary the inputs).

    stop_rule "CLOSE": target (high >= level) first, then a close below invalidation, in
    each candle. "WICK": an open at/below invalidation exits at that open, then a low
    touching invalidation exits at invalidation, then the target. target None: only the
    invalidation and horizon exits, and not-entered rules 1 and 4 (secondary 2).
    """
    if stop_rule not in {"CLOSE", "WICK"}:
        raise ValueError("stop_rule must be CLOSE or WICK")
    k0 = entry_offset(signal_close_ms, decision_time_ms)
    entry_close = signal_close_ms + k0 * INTERVAL_MS
    horizon_end = entry_close + (horizon - 1) * INTERVAL_MS
    base = {"entry_candle_offset": k0, "entry_candle_close_time_ms": entry_close,
            "horizon_bars": horizon, "horizon_end_close_time_ms": horizon_end, "stop_rule": stop_rule}

    def done(status: str, reason: str, r: Decimal | None = None, **extra: Any) -> dict[str, Any]:
        return {**base, "status": status, "reason": reason, "r": r, **extra}

    if horizon_end > cutoff_ms:
        return done("CENSORED", "HORIZON_AFTER_CUTOFF")
    pre_times = [signal_close_ms + step * INTERVAL_MS for step in range(1, k0)]
    present = [tape[t] for t in pre_times if t in tape]
    missing_pre = len(pre_times) - len(present)
    flag = {"pre_entry_path_incomplete": True} if missing_pre else {}
    if any(bar["close"] < invalidation for bar in present):
        return done("NOT_ENTERED", "PRE_ENTRY_INVALIDATED", Decimal(0), **flag)
    if target is not None and any(bar["high"] >= target for bar in present):
        return done("NOT_ENTERED", "PRE_ENTRY_TARGET_TOUCHED", Decimal(0), **flag)
    if missing_pre:
        return done("CENSORED", "MISSING_PRE_ENTRY_CANDLE")
    first = tape.get(entry_close)
    if first is None:
        return done("CENSORED", "MISSING_ENTRY_CANDLE")
    if target is not None and first["open"] >= target:
        return done("NOT_ENTERED", "OPEN_AT_OR_ABOVE_TARGET", Decimal(0))
    if first["open"] <= invalidation:
        return done("NOT_ENTERED", "OPEN_AT_OR_BELOW_INVALIDATION", Decimal(0))
    entry = first["open"]
    for offset in range(horizon):
        close_time = entry_close + offset * INTERVAL_MS
        bar = tape.get(close_time)
        if bar is None:
            return done("CENSORED", "MISSING_PATH_CANDLE", missing_offset=offset, entry_price=entry)
        exit_reason = exit_price = None
        if stop_rule == "CLOSE":
            if target is not None and bar["high"] >= target:
                exit_reason, exit_price = "TARGET", target
            elif bar["close"] < invalidation:
                exit_reason, exit_price = "CLOSE_STOP", bar["close"]
        else:
            if bar["open"] <= invalidation:
                exit_reason, exit_price = "GAP_STOP_AT_OPEN", bar["open"]
            elif bar["low"] <= invalidation:
                exit_reason, exit_price = "WICK_STOP", invalidation
            elif target is not None and bar["high"] >= target:
                exit_reason, exit_price = "TARGET", target
        if exit_reason is None and offset == horizon - 1:
            exit_reason, exit_price = "HORIZON", bar["close"]
        if exit_reason is not None:
            return done("ENTERED", exit_reason, (exit_price - entry) / risk_distance,
                        entry_price=entry, exit_price=exit_price, exit_close_time_ms=close_time,
                        bars_held=offset + 1)
    raise AssertionError("unreachable")


def _case_base(item: Observation, settings: Settings) -> dict[str, Any]:
    return {"candle_close_time_ms": item.candle, "utc_day": utc_day(item.candle),
            "segment": item.segment, "snapshot_index": item.snapshot_index,
            "replay_decision_id": item.row.get("decision_id"), "live_decision_id": item.live_decision_id,
            "live_status": item.mapping_status, "observed_at_ms": item.observed_at_ms,
            "decision_time_ms": item.decision_time_ms, "time_source": item.time_source,
            "signal": item.signal}


def tdal_levels(item: Observation) -> dict[str, Any]:
    risk = item.row["risk"]
    reward = risk.get("reward_reference") or {}
    reference = decimal(risk["reference_price"]["value"])
    invalidation = decimal(risk["invalidation"]["level"])
    distance = decimal(risk["per_unit_risk"]["distance"])
    return {"reference": reference, "invalidation": invalidation, "risk_distance": distance,
            "target": decimal(reward["level"]) if reward.get("status") == "AVAILABLE" else None,
            "distance_matches_reference_minus_invalidation": distance == reference - invalidation}


def comparator_levels(item: Observation, snapshot: dict[str, Any] | None, d: Decimal) -> dict[str, Any]:
    """§4 secondary 1: causal levels from the observation's own snapshot."""
    features = item.row.get("features")
    if not isinstance(features, dict) or snapshot is None:
        return {"excluded": "FEATURES_OR_SNAPSHOT_MISSING"}
    try:
        reference = decimal(features["completed_close"])
        atr = decimal(features["atr_15m"])
    except (KeyError, TypeError, ValueError):
        return {"excluded": "FEATURES_MALFORMED"}
    stop_distance = d * atr
    invalidation = reference - stop_distance
    if atr <= 0 or reference <= 0 or invalidation <= 0:
        return {"excluded": "NON_POSITIVE_LEVELS"}
    reward = assess_reward_reference(
        snapshot, reference=reference, reference_close_time_ms=item.candle, stop_distance=stop_distance,
        atr=atr, breakout_close_time_ms=item.candle, modelled_round_trip_bps=None, config=ObserverConfig())
    if reward.get("status") != "AVAILABLE":
        return {"excluded": "TARGET_UNAVAILABLE", "reward_reason": reward.get("reason")}
    return {"reference": reference, "atr": atr, "invalidation": invalidation,
            "risk_distance": stop_distance, "target": reward["level"]}


# --- statistics -------------------------------------------------------------

def block_key(close_ms: int, length_days: int, origin_ms: int) -> int:
    """Block b covers [T0 + b·L, T0 + (b+1)·L); L = 1 day is the UTC day when T0 is midnight."""
    return (close_ms - origin_ms) // (length_days * DAY_MS)


def percentile_indices(draws: int) -> tuple[int, int]:
    return round(draws * 0.025) - 1, round(draws * 0.975) - 1


_DRAW_CACHE: dict[tuple[int, int, int], list[list[int]]] = {}


def draw_indices(seed: int, draws: int, n: int) -> list[list[int]]:
    """The unit indices of every draw from a fresh random.Random(seed) (CHOICE bootstrap_draw).

    A fresh generator per interval means two intervals over the same number of units draw
    the same indices; they are computed once and reused, which changes no value.
    """
    key = (seed, draws, n)
    if key not in _DRAW_CACHE:
        rng = random.Random(seed)
        _DRAW_CACHE.clear()
        _DRAW_CACHE[key] = [[rng.randrange(n) for _ in range(n)] for _ in range(draws)]
    return _DRAW_CACHE[key]


def bootstrap(units: list[Any], statistic: Callable[[list[int]], Decimal], settings: Settings) -> dict[str, Any]:
    """Percentile bootstrap over units, fresh random.Random(seed) per interval (§4)."""
    base = {"units": len(units), "draws": settings.draws, "seed": settings.seed}
    if len(units) < 2:
        return {**base, "status": "NOT_COMPUTED_FEWER_THAN_2_UNITS", "lower": None, "upper": None}
    values = []
    with localcontext() as ctx:
        ctx.prec = 50
        for picks in draw_indices(settings.seed, settings.draws, len(units)):
            values.append(statistic(picks))
    values.sort()
    low, high = percentile_indices(settings.draws)
    return {**base, "status": "COMPUTED", "lower": values[low], "upper": values[high]}


def ratio_interval(cases: list[dict[str, Any]], value: Callable[[dict[str, Any]], Decimal | None],
                   length_days: int, settings: Settings) -> dict[str, Any]:
    """total / count over drawn blocks; units are blocks holding >= 1 counted case."""
    sums: dict[int, list[Any]] = defaultdict(lambda: [Decimal(0), 0])
    for case in cases:
        v = value(case)
        if v is None:
            continue
        unit = sums[block_key(case["candle_close_time_ms"], length_days, settings.block_origin_ms)]
        unit[0] += v
        unit[1] += 1
    units = [tuple(sums[key]) for key in sorted(sums)]

    def statistic(picks: list[int]) -> Decimal:
        return sum((units[i][0] for i in picks), Decimal(0)) / sum(units[i][1] for i in picks)

    return {"block_days": length_days, **bootstrap(units, statistic, settings)}


def contrast_interval(tdal: list[dict[str, Any]], comparator: list[dict[str, Any]], length_days: int,
                      settings: Settings) -> dict[str, Any]:
    """Joint calendar-aligned resampling: a drawn block brings all its TDAL and comparator cases."""
    def collect(cases):
        sums: dict[int, list[Any]] = defaultdict(lambda: [Decimal(0), 0])
        for case in cases:
            if case["status"] in {"ENTERED", "NOT_ENTERED"}:
                unit = sums[block_key(case["candle_close_time_ms"], length_days, settings.block_origin_ms)]
                unit[0] += case["r"]
                unit[1] += 1
        return sums
    a, b = collect(tdal), collect(comparator)
    keys = sorted(set(a) & set(b))
    units = [(a[k][0], a[k][1], b[k][0], b[k][1]) for k in keys]
    point = None
    if units:
        point = (sum((u[0] for u in units), Decimal(0)) / sum(u[1] for u in units)
                 - sum((u[2] for u in units), Decimal(0)) / sum(u[3] for u in units))

    def statistic(picks: list[int]) -> Decimal:
        ta = sum((units[i][0] for i in picks), Decimal(0))
        na = sum(units[i][1] for i in picks)
        tb = sum((units[i][2] for i in picks), Decimal(0))
        nb = sum(units[i][3] for i in picks)
        return ta / na - tb / nb

    return {"block_days": length_days, "retained_blocks": keys, "estimate": point,
            "blocks_tdal_only": len(set(a) - set(b)), "blocks_comparator_only": len(set(b) - set(a)),
            **bootstrap(units, statistic, settings)}


def scored_r(case: dict[str, Any]) -> Decimal | None:
    return case["r"] if case["status"] in {"ENTERED", "NOT_ENTERED"} else None


def entered_r(case: dict[str, Any]) -> Decimal | None:
    return case["r"] if case["status"] == "ENTERED" else None


def censored_as(score: Decimal) -> Callable[[dict[str, Any]], Decimal]:
    return lambda case: score if case["status"] == "CENSORED" else case["r"]


def arm_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [c for c in cases if c["status"] in {"ENTERED", "NOT_ENTERED"}]
    entered = [c for c in cases if c["status"] == "ENTERED"]
    total = sum((c["r"] for c in scored), Decimal(0))
    entered_total = sum((c["r"] for c in entered), Decimal(0))
    return {
        "cases": len(cases),
        "status_counts": dict(sorted(Counter(c["status"] for c in cases).items())),
        "reason_counts": dict(sorted(Counter(f"{c['status']}:{c['reason']}" for c in cases).items())),
        "scored": len(scored), "entered": len(entered), "censored": len(cases) - len(scored),
        "total_r": total, "mean_r": total / len(scored) if scored else None,
        "entered_total_r": entered_total, "entered_mean_r": entered_total / len(entered) if entered else None,
        "utc_days_with_scored_case": len({c["utc_day"] for c in scored}),
        "utc_days_with_entered_trade": len({c["utc_day"] for c in entered}),
    }


def sparse(summary: dict[str, Any], settings: Settings) -> bool:
    return (summary["entered"] < settings.sparse_min_entered
            or summary["utc_days_with_entered_trade"] < settings.sparse_min_entered_days)


def read_interval(interval: dict[str, Any], *, is_sparse: bool, positive: str, negative: str) -> str:
    """§4 reading, fixed in advance; strict inequalities at the boundaries."""
    if is_sparse:
        return "INCONCLUSIVE_SPARSE"
    if interval.get("status") != "COMPUTED":
        return "INCONCLUSIVE_NO_INTERVAL"
    if interval["lower"] > 0:
        return positive
    if interval["upper"] < 0:
        return negative
    return "INCONCLUSIVE"


def intervals(cases, value, settings: Settings, *, day_only: bool = False) -> dict[str, Any]:
    lengths = (1,) if day_only else settings.block_lengths_days
    return {("utc_day" if days == 1 else f"block_{days}d"): ratio_interval(cases, value, days, settings)
            for days in lengths}


# --- secondaries that reuse repository code ----------------------------------

def first_touch_descriptive(eligible: list[Observation], tape, cutoff_ms: int, horizon: int) -> dict[str, Any]:
    """Secondary 4: the harness's _first_touch from the signal close (the original primary)."""
    rows = []
    for item in eligible:
        times = [item.candle + step * INTERVAL_MS for step in range(1, horizon + 1)]
        if times[-1] > cutoff_ms:
            label, path = {"status": "CENSORED", "reason": "horizon_after_cutoff"}, None
        elif any(t not in tape for t in times):
            label, path = {"status": "CENSORED", "reason": "path_not_observed"}, None
        else:
            label, path = {"status": "AVAILABLE"}, [tape[t] for t in times]
        rows.append({"candle_close_time_ms": item.candle,
                     "reward_first_touch": _first_touch(item.row.get("risk"), label, path)})
    return {"horizon_bars": horizon, "path_start": "SIGNAL_CLOSE", "summary": _first_touch_summary(rows),
            "primary_rule_of_original_plan": "close_stop", "cases": rows,
            "note": "Mechanism description, not an edge measure (§4 secondary 4)."}


def forward_baselines(first: list[Observation], tape, cutoff_ms: int, bars: int) -> dict[str, Any]:
    """Secondary 5: harness forward moves, TDAL vs SELECT_ALL and POSITIVE_PRIOR_RETURN."""
    rows = []
    for item in first:
        try:
            label = _label_decision(item.row, tape, INTERVAL_MS, (bars,))["horizons"].get(str(bars))
        except OutcomeLabelError as exc:
            label = {"status": "UNAVAILABLE", "reason": str(exc)}
        if label is None or item.candle + bars * INTERVAL_MS > cutoff_ms:
            label = {"status": "CENSORED", "reason": "horizon_exceeds_evaluation_window"}
        features = item.row.get("features") or {}
        try:
            prior = decimal(features["return_15m_pct"]) > 0
        except (KeyError, TypeError, ValueError):
            prior = False
        rows.append({"selected": {"TDAL": item.signal == "LONG_ELIGIBLE", "POSITIVE_PRIOR_RETURN": prior,
                                  "SELECT_ALL": True, "ABSTAIN_ALL": False}, "outcome": label})
    policies = {name: _policy_metrics(rows, name) for name in ("TDAL", "SELECT_ALL", "POSITIVE_PRIOR_RETURN")}
    tdal, everyone = (policies[k]["mean_selected_forward_return_pct"] for k in ("TDAL", "SELECT_ALL"))
    return {"horizon_bars": bars, "path_start": "SIGNAL_CLOSE", "policies": policies,
            "tdal_minus_select_all_pct_points": tdal - everyone if tdal is not None and everyone is not None else None,
            "note": "Descriptive (§4 secondary 5)."}


def paper_ledger(eligible: list[Observation], tape, cutoff_ms: int, settings: Settings,
                 dataset_id: str, replay_id: str) -> dict[str, Any]:
    """Secondary 6: the merged RiskGovernor on one $1,000 paper account per model and tier."""
    decisions = []
    for item in eligible:
        row = json.loads(json.dumps(item.row))
        row["observed_at_ms"] = item.decision_time_ms  # CHOICE ledger_entry_time
        decisions.append(row)
    candles = [dict(tape[key]) for key in sorted(tape) if key <= cutoff_ms]
    carrier = {"observed_at_ms": cutoff_ms + 1, "exchange_time_ms": cutoff_ms + 1,
               "research_symbol": "TAOUSDT", "executable_pair": "TAOUSD", "candles_15m": candles}
    runs = {}
    for tier, taker, maker in settings.ledger_fee_tiers:
        for model in ("MAKER_STAGGERED", "MARKET_BASELINE"):
            key = f"{model}/{tier}"
            try:
                artifact = build_governed_paper_artifact(
                    [carrier], {"replay_id": replay_id, "decisions": decisions}, dataset_id=dataset_id,
                    paper_config=PaperExecutionConfig(fee_bps_per_side=taker,
                                                      slippage_bps_per_side=settings.ledger_slippage_bps,
                                                      max_hold_bars=settings.ledger_max_hold_bars),
                    config=GovernorConfig(execution_model=model, maker_fee_bps=maker))
            except (GovernorError, PaperExecutionError, OutcomeLabelError, ValueError) as exc:
                runs[key] = {"status": "NOT_RUN", "reason": f"{type(exc).__name__}: {exc}",
                             "taker_fee_bps": taker, "maker_fee_bps": maker}
                continue
            runs[key] = {"status": "RUN", "taker_fee_bps": taker, "maker_fee_bps": maker,
                         **_ledger_metrics(artifact), "artifact": artifact}
    return {"order": ["MAKER_STAGGERED/TIER_4_OWNER", "MARKET_BASELINE/TIER_4_OWNER",
                      "MAKER_STAGGERED/TIER_1_SENSITIVITY", "MARKET_BASELINE/TIER_1_SENSITIVITY"],
            "fee_basis": "Dated assumptions read 2026-10-13 from kraken.com/features/fee-schedule "
                         "(Kraken Pro spot); Tier 1 is a sensitivity, not the owner's tier.",
            "slippage_bps_per_side": settings.ledger_slippage_bps,
            "max_hold_bars": settings.ledger_max_hold_bars,
            "decisions_offered": len(decisions), "runs": runs,
            "limitations": ["Maker fills are optimistic (no queue, partial fills or adverse selection)",
                            "Fills are priced on Binance USD-M candles, not Kraken",
                            "A positive ledger alone is not a success and cannot rescue the primary"]}


def _ledger_metrics(artifact: dict[str, Any]) -> dict[str, Any]:
    journal, trades = artifact["journal"], artifact["trades"]
    approved = [row for row in journal if row["status"] == "APPROVED_FOR_PAPER"]
    filled = [t for t in trades if t["status"] == "FILLED"]
    shares = [row["quantity"] * row["sizing_reference_price"] * row["round_trip_cost_bps"] / 10000
              / row["risk_budget_usd"] for row in approved if row["risk_budget_usd"] > 0]
    breakers = {}
    for reason in ("DAILY_LOSS_LIMIT_REACHED", "CONSECUTIVE_LOSS_LIMIT_REACHED"):
        hits = [row for row in journal if reason in row.get("reasons", [])]
        breakers[reason] = {"blocked_proposals": len(hits),
                            "utc_days": len({row["account_before"]["utc_day"] for row in hits})}
    summary = artifact["summary"]
    return {
        "ending_equity_usd": summary["ending_equity_usd"],
        "ending_equity_basis": summary["ending_equity_basis"],
        "net_r": sum((t["realized_r"] for t in filled), Decimal(0)),
        "max_realized_drawdown_usd": summary["max_realized_drawdown_usd"],
        "approved": len(approved), "filled": len(filled),
        "entry_fill_rate": Decimal(len(filled)) / len(approved) if approved else None,
        "cost_share_of_risk_mean": sum(shares, Decimal(0)) / len(shares) if shares else None,
        "breaker_trips": breakers,
        "paper_outcome_counts": dict(sorted(Counter(row.get("paper_outcome") for row in approved).items())),
    }


# --- coverage ---------------------------------------------------------------

def runs_of(times: list[int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for t in times:
        if result and t == result[-1]["last_close_time_ms"] + INTERVAL_MS:
            result[-1]["last_close_time_ms"] = t
            result[-1]["candles"] += 1
        else:
            result.append({"first_close_time_ms": t, "last_close_time_ms": t, "candles": 1})
    return result


def coverage(observations: list[Observation], selection: dict[str, Any], tape, recorder_gaps, failures,
             settings: Settings, cutoff_ms: int, classes: Counter, export: ExportInputs) -> dict[str, Any]:
    first_close = settings.holdout_start_ms + (-(settings.holdout_start_ms + 1) % INTERVAL_MS)
    expected = list(range(first_close, cutoff_ms, INTERVAL_MS))
    window = [o for o in observations if settings.holdout_start_ms <= o.observed_at_ms <= cutoff_ms]
    with_row = {implied_candle(o) for o in window}
    with_first = {o.candle for o in selection["first"]}
    no_live = [{"segment": o.segment, "snapshot_index": o.snapshot_index, "observed_at_ms": o.observed_at_ms}
               for o in window if o.mapping_status == "NO_LIVE_DECISION"]
    eligible = classes["TARGETED"] + classes["TARGET_UNAVAILABLE"] + classes["ELIGIBLE_RISK_UNAVAILABLE"]
    live_failures = [f for f in failures if settings.holdout_start_ms <= f["decision_time_ms"] <= cutoff_ms]
    rows = selection["counts"].get("rows_in_window", 0)
    blocked = selection["counts"].get("blocked_rows", 0)
    return {
        "expected_candles": len(expected),
        "candles_with_any_pooled_row": sum(t in with_row for t in expected),
        "uptime_share": Decimal(sum(t in with_row for t in expected)) / len(expected) if expected else None,
        "candles_with_first_gate_passing_observation": len(with_first),
        "decision_gaps": runs_of([t for t in expected if t not in with_first]),
        "price_gaps": runs_of([t for t in expected if t not in tape]),
        "recorder_gaps": recorder_gaps,
        "rows_in_window": rows, "blocked_rows": blocked,
        "live_failures_without_snapshot": len(live_failures),
        "blocked_cycle_share": (Decimal(blocked + len(live_failures)) / (rows + len(live_failures))
                                if rows + len(live_failures) else None),
        "first_observation_classes": dict(sorted(classes.items())),
        "target_unavailable_share_of_eligible": (Decimal(classes["TARGET_UNAVAILABLE"]) / eligible
                                                 if eligible else None),
        "no_live_decision_rows": no_live,
        "duplicate_rows": selection["counts"].get("duplicate_rows", 0),
        "changed_signal_duplicates": selection["changed_signal_duplicates"],
        "rows_not_pooled_by_segment": {name: record.get("rows_not_pooled") for name, record
                                       in export.population_manifest["segments"].items()},
        "effective_cutover_candle_close_ms": export.population_manifest.get("effective_cutover_candle_close_ms"),
        "cutover_moved": export.population_manifest.get("cutover_moved"),
        "seam_report": {"seam": export.seam_report.get("seam"), "overlap": export.seam_report.get("overlap")},
    }


# --- the run ----------------------------------------------------------------

def code_revision() -> dict[str, Any]:
    here = Path(__file__).resolve()
    info: dict[str, Any] = {"analysis_version": ANALYSIS_VERSION,
                            "analysis_sha256": sha256_file(here),
                            "inputs_adapter_sha256": sha256_file(here.parent / "inputs.py")}
    try:
        head = subprocess.run(["git", "-C", str(here.parent), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(here.parent), "status", "--porcelain", "--", "."],
                               capture_output=True, text=True, check=True).stdout.strip()
        info.update(git_head=head, analysis_directory_dirty=bool(dirty))
    except (OSError, subprocess.CalledProcessError):
        info.update(git_head=None, analysis_directory_dirty=None)
    return info


def run_analysis(export_dir: Path, *, settings: Settings = Settings(), cutoff_ms: int | None = None,
                 plan_path: Path | None = None) -> dict[str, Any]:
    export = load_export(export_dir, expected_replay_version=settings.expected_replay_version)
    observations = export.rows
    check_population_flags(observations)

    stop = derive_cutoff(observations, settings)
    if not stop["met"] and settings.partition == "HOLDOUT_CONFIRMATORY":
        # No interim look (§5): a holdout run before the trigger stops here, whatever cut-off is asked.
        raise AnalysisHalt(f"§5 trigger not met (targeted so far {stop.get('targeted_so_far')}); "
                           "the holdout analysis cannot run before the trigger.")
    if cutoff_ms is None:
        if not stop["met"]:
            raise AnalysisHalt(f"§5 trigger not met (targeted so far {stop.get('targeted_so_far')}); "
                               "no analysis before the trigger. Pass --cutoff-ms only for a labelled check.")
        cutoff, cutoff_source = stop["cutoff_ms"], "DERIVED_SECTION_5"
    else:
        cutoff, cutoff_source = cutoff_ms, "EXPLICIT_ARGUMENT"
    selection = first_observations(observations, settings.holdout_start_ms, cutoff)
    classes = Counter(classify(item) for item in selection["first"])
    groups: dict[str, list[Observation]] = defaultdict(list)
    for item in selection["first"]:
        groups[classify(item)].append(item)
    pooled_by_key = {(item.segment, item.snapshot_index): item for item in observations}
    comparator_by_key = {(item.segment, item.snapshot_index): item for item in groups["NOT_ELIGIBLE"]}

    # Stream snapshots: verify each dataset manifest and the pooled rows' snapshot IDs, build
    # the price tape, and compute comparator levels from each comparator candle's own snapshot
    # (levels kept, snapshots dropped: memory stays bounded).
    tape: dict[int, dict[str, Any]] = {}
    comparator_levels_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    recorder_gaps: list[dict[str, Any]] = []
    datasets = []
    for name in export.segments:
        count, previous = 0, None
        replay = export.replays[name]
        for index, snapshot in stream_verified_snapshots(export.segment_file(name, "snapshots.jsonl"),
                                                         export.dataset_manifests[name]):
            count += 1
            observed = snapshot["observed_at_ms"]
            if (previous is not None and observed - previous > settings.poll_gap_ms
                    and observed >= settings.holdout_start_ms and previous <= cutoff):
                recorder_gaps.append({"segment": name, "from_ms": previous, "to_ms": observed,
                                      "ms": observed - previous})
            previous = observed
            mine = pooled_by_key.get((name, index))
            if mine is not None:
                if observed != mine.observed_at_ms:
                    raise AnalysisHalt(f"{name} snapshot {index}: observed_at_ms differs from the population")
                if snapshot_id(snapshot) != mine.snapshot_id:
                    raise AnalysisHalt(f"{name} snapshot {index}: recomputed snapshot_id differs (§3)")
            if observed <= cutoff:
                add_snapshot_candles(tape, snapshot, name, export.supplier)
            if (name, index) in comparator_by_key:
                comparator_levels_by_key[(name, index)] = comparator_levels(
                    comparator_by_key[(name, index)], snapshot, settings.comparator_d)
        if count != replay["decisions"]:
            raise AnalysisHalt(f"{name}: {count} snapshots but {replay['decisions']} replay rows")
        manifest = export.dataset_manifests[name]
        datasets.append({"segment": name, "dataset_id": manifest["dataset_id"],
                         "jsonl_sha256": manifest["jsonl_sha256"], "snapshot_count": count, **replay,
                         "live_failures_without_snapshot":
                             len(export.parity[name].get("live_decisions_without_snapshot") or [])})
    comparator_inputs = [(item, comparator_levels_by_key.get((item.segment, item.snapshot_index),
                                                             {"excluded": "FEATURES_OR_SNAPSHOT_MISSING"}))
                         for item in groups["NOT_ELIGIBLE"]]

    def run_cases(items, horizon, stop_rule, with_target=True):
        cases = []
        for item, levels in items:
            case = {**_case_base(item, settings),
                    **{k: levels[k] for k in ("reference", "invalidation", "risk_distance", "target")}}
            case.update(simulate_trade(
                signal_close_ms=item.candle, decision_time_ms=item.decision_time_ms,
                invalidation=levels["invalidation"], target=levels["target"] if with_target else None,
                risk_distance=levels["risk_distance"], tape=tape, horizon=horizon, stop_rule=stop_rule,
                cutoff_ms=cutoff))
            cases.append(case)
        return cases

    targeted = [(item, tdal_levels(item)) for item in groups["TARGETED"]]
    target_unavailable = [(item, tdal_levels(item)) for item in groups["TARGET_UNAVAILABLE"]]
    primary_cases = run_cases(targeted, settings.primary_horizon, "CLOSE")
    primary_summary = arm_summary(primary_cases)
    primary_intervals = intervals(primary_cases, scored_r, settings)
    is_sparse = sparse(primary_summary, settings)
    primary = {
        "definition": "Mean gross R over every scored case (entered and not entered), entry at the "
                      "first candle opening at/after the decision time, 16 candles, close stop, "
                      "R against the decision's original risk distance (§4).",
        "summary": primary_summary,
        "intervals": primary_intervals,
        "entered_only": {"mean_r": primary_summary["entered_mean_r"],
                         "intervals": intervals(primary_cases, entered_r, settings)},
        "censoring_sensitivity": {
            label: {"mean_r": _mean([censored_as(score)(c) for c in primary_cases]),
                    "intervals": intervals(primary_cases, censored_as(score), settings)}
            for label, score in (("censored_as_0R", Decimal(0)), ("censored_as_minus_1R", Decimal(-1)))},
        "sparse": is_sparse,
        "reading": read_interval(primary_intervals["utc_day"], is_sparse=is_sparse,
                                 positive="POSITIVE_GROSS_BEFORE_COSTS", negative="EVIDENCE_AGAINST_RULE"),
        "reading_basis": "UTC-day interval, censored cases excluded (§4 reading rules)",
        "risk_distance_mismatches": sum(not lv["distance_matches_reference_minus_invalidation"]
                                        for _, lv in targeted),
        "entry_offset_counts": dict(sorted(Counter(str(c["entry_candle_offset"]) for c in primary_cases).items())),
        "cases": primary_cases,
    }

    comparator_ok = [(item, lv) for item, lv in comparator_inputs if "excluded" not in lv]
    comparator_cases = run_cases(comparator_ok, settings.primary_horizon, "CLOSE")
    contrast = {("utc_day" if d == 1 else f"block_{d}d"): contrast_interval(primary_cases, comparator_cases, d, settings)
                for d in settings.block_lengths_days}
    day_keys = set(contrast["utc_day"]["retained_blocks"])
    in_day = lambda c: block_key(c["candle_close_time_ms"], 1, settings.block_origin_ms) in day_keys  # noqa: E731
    contrast_tdal = arm_summary([c for c in primary_cases if in_day(c)])
    contrast_comp = arm_summary([c for c in comparator_cases if in_day(c)])
    contrast_sparse = sparse(contrast_tdal, settings) or contrast_comp["entered"] < settings.comparator_min_entered
    comparator = {
        "d_atr": settings.comparator_d,
        "candidates": len(comparator_inputs),
        "exclusions": dict(sorted(Counter(lv["excluded"] for _, lv in comparator_inputs if "excluded" in lv).items())),
        "summary_all_window": arm_summary(comparator_cases),
        "contrast": contrast,
        "contrast_tdal_cases": contrast_tdal, "contrast_comparator_cases": contrast_comp,
        "sparse": contrast_sparse,
        "reading": read_interval(contrast["utc_day"], is_sparse=contrast_sparse,
                                 positive="TDAL_SELECTS_BETTER_THAN_COMPARATOR",
                                 negative="TDAL_SELECTS_WORSE_THAN_COMPARATOR"),
        "limits": "TDAL's stop is structural, the comparator's a fixed ATR multiple: comparable in "
                  "scale, not identical in geometry.",
        "cases": comparator_cases,
        "excluded_cases": [{**_case_base(item, settings), **{k: v for k, v in lv.items()}}
                           for item, lv in comparator_inputs if "excluded" in lv],
    }

    tu_cases = run_cases(target_unavailable, settings.primary_horizon, "CLOSE", with_target=False)
    variants = {}
    for horizon in settings.close_horizons_secondary:
        variants[f"close_{horizon}"] = run_cases(targeted, horizon, "CLOSE")
    for horizon in settings.wick_horizons:
        variants[f"wick_{horizon}"] = run_cases(targeted, horizon, "WICK")
    eligible = [item for item in selection["first"] if item.signal == "LONG_ELIGIBLE"]
    dataset_id = stable_id("pooled_datasets", [d["dataset_id"] for d in datasets])
    replay_id = stable_id("pooled_replays", [d["replay_id"] for d in datasets])
    secondary = {
        "1_comparator": comparator,
        "2_target_unavailable": {"summary": arm_summary(tu_cases),
                                 "intervals": intervals(tu_cases, scored_r, settings, day_only=True),
                                 "cases": tu_cases},
        "3_other_horizons_and_stops": {
            key: {"summary": arm_summary(cases), "intervals": intervals(cases, scored_r, settings, day_only=True),
                  "cases": cases} for key, cases in variants.items()},
        "4_first_touch_descriptive": first_touch_descriptive(eligible, tape, cutoff, settings.primary_horizon),
        "5_forward_move_baselines": forward_baselines(selection["first"], tape, cutoff,
                                                      settings.forward_baseline_bars),
        "6_governed_paper_ledger": paper_ledger(eligible, tape, cutoff, settings, dataset_id, replay_id),
        "7_coverage": coverage(observations, selection, tape, recorder_gaps,
                               [{"segment": name, **item} for name in export.segments
                                for item in export.parity[name].get("live_decisions_without_snapshot") or []],
                               settings, cutoff, classes, export),
    }
    deviations = []
    if cutoff_source == "EXPLICIT_ARGUMENT" and (not stop["met"] or stop["cutoff_ms"] != cutoff):
        deviations.append("Cut-off given explicitly and not the §5 derived cut-off.")
    if settings != replace(Settings(), partition=settings.partition) or settings.partition != "HOLDOUT_CONFIRMATORY":
        deviations.append("Settings differ from the plan's fixed values: not a holdout analysis.")
    content = {
        "schema_version": "prospective_v1_analysis/0.1",
        "partition": settings.partition,
        "code_revision": code_revision(),
        "plan": ({"path": str(plan_path), "sha256": sha256_file(plan_path)} if plan_path else None),
        "settings": settings.public(),
        "inputs": {"export_directory_name": Path(export_dir).name,
                   "fingerprints_sha256": export.fingerprints, "datasets": datasets,
                   "pinned_code_commit": export.population_manifest.get("pinned_code_commit"),
                   "export_tooling_code_revision": export.population_manifest.get("tooling_code_revision"),
                   "dataset_id": dataset_id, "replay_id": replay_id},
        "window": {"holdout_start_ms": settings.holdout_start_ms, "cutoff_ms": cutoff,
                   "cutoff_source": cutoff_source, "stopping_rule": stop,
                   "targeted_cases_at_cutoff": classes["TARGETED"]},
        "population": {"first_observation_classes": dict(sorted(classes.items())),
                       "selection_counts": selection["counts"],
                       "time_source_counts": dict(sorted(Counter(i.time_source for i in selection["first"]).items()))},
        "primary": primary,
        "secondary": secondary,
        "deviations": deviations,
        "resolved_ambiguities": RESOLVED_AMBIGUITIES,
        "claims": {"gross_of_costs": True, "profitability_claim": False, "calibrated_probability": False,
                   "execution_permission": "NONE", "only_inference": "primary.reading"},
    }
    content = json.loads(canonical_json(content))
    return {"result_id": stable_id("prospective_v1_analysis", content), **content}


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def _fmt(value: Any) -> str:
    """Four decimals for the human summary only; the JSON keeps every digit."""
    if value is None:
        return "n/a"
    return str(Decimal(value).quantize(Decimal("0.0001")))


def summary_text(result: dict[str, Any]) -> str:
    p = result["primary"]
    s = p["summary"]
    day = p["intervals"]["utc_day"]
    comp = result["secondary"]["1_comparator"]
    cday = comp["contrast"]["utc_day"]
    sens = p["censoring_sensitivity"]
    window = result["window"]
    lines = [
        f"# Prospective v1 analysis ({result['partition']})",
        "",
        f"- Result `{result['result_id']}`; analysis code {result['code_revision'].get('git_head')}",
        f"- Window (observed_at_ms): {window['holdout_start_ms']} to {window['cutoff_ms']} ({window['cutoff_source']})",
        f"- Targeted cases {s['cases']}: scored {s['scored']}, entered {s['entered']}, censored {s['censored']}; "
        f"UTC days with an entered trade {s['utc_days_with_entered_trade']}",
        f"- Status/reason counts: {json.dumps(s['reason_counts'], sort_keys=True)}",
        f"- **Primary mean gross R {_fmt(s['mean_r'])}**, 95% UTC-day interval "
        f"[{_fmt(day['lower'])}, {_fmt(day['upper'])}] ({day['status']}, {day['units']} days)",
        f"- **Reading: {p['reading']}**",
        f"- Entered-only mean R {_fmt(s['entered_mean_r'])}; censored scored 0R / -1R: "
        f"{_fmt(sens['censored_as_0R']['mean_r'])} / {_fmt(sens['censored_as_minus_1R']['mean_r'])}",
        f"- Comparator contrast (TDAL - comparator, D = {comp['d_atr']} ATR) {_fmt(cday['estimate'])}, "
        f"interval [{_fmt(cday['lower'])}, {_fmt(cday['upper'])}]: {comp['reading']}",
        f"- Deviations: {len(result['deviations'])}",
        "",
        "Gross of costs; not a profitability claim. Every case, horizon, interval and ledger journal "
        "line is in the JSON.",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(result: dict[str, Any], output: Path) -> Path:
    """Exclusive create of the JSON and its summary; neither may exist."""
    summary_path = output.with_suffix(".summary.md")
    if output.exists() or summary_path.exists():
        raise FileExistsError("analysis outputs must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, text in ((output, canonical_json(result) + "\n"), (summary_path, summary_text(result))):
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                handle.write(text)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return summary_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="TDAL prospective v1 analysis (one run, §5)")
    result.add_argument("--export-dir", required=True,
                        help="OUT of `prospective_data.py all` (segment1/, segment2/, pooled/)")
    result.add_argument("--output", required=True, help="new result JSON; summary goes to .summary.md")
    result.add_argument("--population-manifest-sha256", default=None,
                        help="SHA-256 recorded for pooled/population_manifest.json; refuse if it differs")
    result.add_argument("--cutoff-ms", type=int, default=None,
                        help="override the derived §5 cut-off (recorded as a deviation)")
    result.add_argument("--plan", default=None, help="PREREGISTRATION.md to fingerprint into the result")
    result.add_argument("--non-holdout-smoke", action="store_true",
                        help="label the run NON_HOLDOUT_SMOKE; allows --window-start-ms and any replay version")
    result.add_argument("--window-start-ms", type=int, default=None, help="smoke only")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings()
    if args.non_holdout_smoke:
        start = args.window_start_ms if args.window_start_ms is not None else settings.holdout_start_ms
        settings = replace(settings, partition="NON_HOLDOUT_SMOKE", holdout_start_ms=start,
                           block_origin_ms=start - start % DAY_MS, expected_replay_version=None)  # type: ignore[arg-type]
    elif args.window_start_ms is not None:
        parser().error("--window-start-ms needs --non-holdout-smoke")
    try:
        if args.population_manifest_sha256 is not None:
            actual = sha256_file(Path(args.export_dir) / "pooled" / "population_manifest.json")
            if actual != args.population_manifest_sha256:
                raise InputError("population_manifest.json does not match --population-manifest-sha256")
        result = run_analysis(Path(args.export_dir), settings=settings, cutoff_ms=args.cutoff_ms,
                              plan_path=Path(args.plan) if args.plan else None)
    except (InputError, AnalysisHalt, OSError) as exc:
        print(f"HALT: {exc}", file=sys.stderr)
        return 2
    summary_path = write_outputs(result, Path(args.output))
    print(json.dumps({"result_id": result["result_id"], "reading": result["primary"]["reading"],
                      "output": args.output, "summary": str(summary_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
