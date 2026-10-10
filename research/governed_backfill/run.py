"""Plumbing check: run the paper-only RiskGovernor over the 29-day TAOUSDT backfill.

Exploratory, not evidence of performance. Reuses the setup-frequency replay and
its assumptions (see research/setup_frequency/history.py). Governor parameters
are the defaults fixed by the repository owner before this run; cost and hold
assumptions are stated below and were not varied.

Usage: python research/governed_backfill/run.py <backfill_dir>
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup_frequency"))
from history import SPREAD, replay  # noqa: E402

from market_observer.governor import GovernorConfig, build_governed_paper_artifact  # noqa: E402
from market_observer.paper import PaperExecutionConfig  # noqa: E402
from market_observer.util import canonical_json  # noqa: E402

FEE_BPS = Decimal("40")  # assumed Kraken spot taker fee at the lowest volume tier
SLIPPAGE_BPS = Decimal("5")
MAX_HOLD_BARS = 16  # the preregistered first-touch primary horizon (4 h)

bars, _, _, _, decisions = replay(sys.argv[1])
rows = [{"decision_id": f"bar{d['bar_index']}", "signal": "LONG_ELIGIBLE",
         "completed_candle_close_time_ms": d["close_time_ms"],
         "features": {"spread_bps": SPREAD * 10000}, "risk": d["risk"]} for d in decisions]
last = bars[-1]["close_time_ms"] + 1
tape_snapshot = {"observed_at_ms": last, "exchange_time_ms": last, "research_symbol": "TAOUSDT",
                 "executable_pair": "TAOUSD", "candles_15m": bars}
result = build_governed_paper_artifact(
    [tape_snapshot], {"replay_id": "backfill_30d_exploratory", "decisions": rows},
    dataset_id="backfill_30d_exploratory",
    paper_config=PaperExecutionConfig(fee_bps_per_side=FEE_BPS, slippage_bps_per_side=SLIPPAGE_BPS,
                                      max_hold_bars=MAX_HOLD_BARS),
    config=GovernorConfig(),
)
approved = [row for row in result["journal"] if row["status"] == "APPROVED_FOR_PAPER"]
cost_share = [row["quantity"] * row["sizing_reference_price"] * row["round_trip_cost_bps"] / 10000
              / row["risk_budget_usd"] for row in approved]
exits = {}
for trade in result["trades"]:
    exits[trade["exit"]["reason"]] = exits.get(trade["exit"]["reason"], 0) + 1
print(canonical_json({
    "summary": result["summary"],
    "exit_reason_counts": dict(sorted(exits.items())),
    "sum_realized_r": sum(trade["realized_r"] for trade in result["trades"]),
    "mean_cost_share_of_risk_budget": sum(cost_share) / len(cost_share) if cost_share else None,
}))
