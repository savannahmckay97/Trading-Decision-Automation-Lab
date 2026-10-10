"""Preregistered exploratory first-touch analysis (see PREREGISTRATION.md).

Reuses the setup-frequency replay and the evaluation harness's first-touch
labelling unchanged. Exploratory only: no parameter may be changed on the
strength of these results.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup_frequency"))
from history import replay  # noqa: E402

from market_observer.evaluation import _first_touch, _first_touch_summary  # noqa: E402
from market_observer.util import canonical_json  # noqa: E402

HORIZONS = (16, 4, 96)  # primary first, then secondary
bars, counts, _, _, decisions = replay(sys.argv[1])
report = {"decision_count": len(decisions), "horizons": {}}
for horizon in HORIZONS:
    rows = []
    for decision in decisions:
        i = decision["bar_index"]
        path = bars[i + 1: i + 1 + horizon]
        complete = len(path) == horizon and all(
            b["open_time_ms"] == bars[i]["open_time_ms"] + (k + 1) * 900_000 for k, b in enumerate(path))
        label = {"status": "AVAILABLE"} if complete else {"status": "CENSORED", "reason": "path_beyond_data"}
        rows.append({"close_time_ms": decision["close_time_ms"],
                     "reward_first_touch": _first_touch(decision["risk"], label, path if complete else None)})
    report["horizons"][str(horizon)] = {"summary": _first_touch_summary(rows), "decisions": rows}
print(canonical_json(report))
