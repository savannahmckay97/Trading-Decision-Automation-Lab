"""Diagnostic: how often would BREAKOUT_RETEST_V1 fire on downloaded TAOUSDT history?

Counts setups only; computes no forward outcomes. See history.py for assumptions.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import replay  # noqa: E402

bars, counts, quality_hist, transitions, decisions = replay(sys.argv[1])
days = counts["bars_considered"] / 96
print(json.dumps({
    "days_considered": round(days, 2), "counts": counts,
    "range_breaks_by_quality_score": dict(sorted(quality_hist.items())),
    "transitions": {k: v for k, v in transitions.items() if k != "INITIALIZED"},
    "long_eligible": len(decisions),
    "reward_status_at_eligible": Counter(d["risk"]["reward_reference"]["status"] for d in decisions),
    "eligible_per_30_days": round(len(decisions) / days * 30, 1) if days else None,
}, indent=1, default=str))
