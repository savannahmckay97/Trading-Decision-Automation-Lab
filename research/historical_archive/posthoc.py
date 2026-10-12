"""Post-hoc descriptive additions to the historical replay results. NOT PREREGISTERED.

Usage: python research/historical_archive/posthoc.py ARCHIVE_DIR REGISTERED_RESULTS_JSON OUT_JSON

Written after the registered run (run.py, results_v1.json) in response to review of PR #21.
Nothing here replaces or re-reads the registered primary; these are descriptive figures that
RESULTS.md quotes, each labelled post-hoc there. Every figure is computed here rather than by
hand. The script:
  1. checks REGISTERED_RESULTS_JSON's SHA-256 is the published one, and the archive's manifest
     is the registered one (archive.py re-hashes every file);
  2. reruns the same replay and run.py's labelling, and checks the 16-bar labels it gets are
     identical to the registered primary_rows;
  3. prints and writes (exclusive creation; an existing OUT_JSON is an error) the figures.

Cost assumption (PREREGISTRATION.md section 3 paper-ledger costs, Kraken Tier 4): maker entry
20 bps + taker exit 35 bps + slippage 2 x 5 bps + spread 3.35 bps = 68.35 bps of price per
round trip, expressed in R as 0.006835 / (stop distance / reference price).
"""
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402
from archive import load_archive  # noqa: E402
from replay_archive import replay  # noqa: E402

from market_observer.evaluation import _first_touch  # noqa: E402
from market_observer.util import canonical_json  # noqa: E402

REGISTERED_RESULTS_SHA256 = "0b915fa6541394973b3c10ca3227db5f4d851a28bb0be50a076b1bb21cd66e37"
ROUND_TRIP_COST = Decimal("0.006835")  # 20 + 35 + 2*5 + 3.35 bps


def ci_block(cases):
    """Mean and the registered day-cluster bootstrap (same draws and seed) for [(day, R)]."""
    lo, hi = run.bootstrap_mean_by_day(cases)
    vals = [float(v) for _, v in cases]
    return {"n": len(vals), "days": len({d for d, _ in cases}),
            "mean_r": sum(vals) / len(vals) if vals else None, "ci95_day_bootstrap": [lo, hi]}


def wick_ambiguous_as_stop(touch):
    if touch.get("status") != "LABELED":
        return None
    result = touch["wick_stop"]["result"]
    if result == "TARGET_FIRST":
        return Decimal(touch["gross_opportunity_to_risk"])
    if result in ("STOP_FIRST", "AMBIGUOUS_SAME_BAR"):
        return Decimal(-1)
    return None


def compute(bars, decisions):
    pop = [d for d in decisions if run.WINDOW_START_MS <= d["close_time_ms"] < run.WINDOW_END_MS]
    rows = []
    for d in pop:
        path = run.path_after(bars, d["bar_index"], run.PRIMARY_HORIZON)
        label = {"status": "AVAILABLE"} if path else {"status": "CENSORED", "reason": "path_beyond_data"}
        rows.append({"close_time_ms": d["close_time_ms"], "setup_id": d["setup_id"],
                     "reward_first_touch": _first_touch(d["risk"], label, path), "_path": path, "_decision": d})

    status = Counter(r["reward_first_touch"]["status"] for r in rows)
    day = lambda r: run.utc_day(r["close_time_ms"])  # noqa: E731
    decided = [r for r in rows if run.realised_r(r["reward_first_touch"], run.PRIMARY_RULE) is not None]
    labelled = [r for r in rows if r["reward_first_touch"]["status"] == "LABELED"]

    close_exit = [(day(r), run.close_exit_r(r["reward_first_touch"], r["_decision"], r["_path"])) for r in decided]
    wick_amb = [(day(r), v) for r in rows if (v := wick_ambiguous_as_stop(r["reward_first_touch"])) is not None]
    stops = [-float(v) for (_, v), r in zip(close_exit, decided)
             if r["reward_first_touch"]["close_stop"]["result"] == "STOP_FIRST"]
    targets = [float(r["reward_first_touch"]["gross_opportunity_to_risk"]) for r in decided
               if r["reward_first_touch"]["close_stop"]["result"] == "TARGET_FIRST"]
    bar1 = Counter(r["reward_first_touch"]["close_stop"]["result"] for r in decided
                   if r["reward_first_touch"]["close_stop"]["bars"] == 1)

    def stop_frac(r):
        risk = r["_decision"]["risk"]
        return Decimal(risk["per_unit_risk"]["distance"]) / Decimal(risk["reference_price"]["value"])

    def cost_block(subset):
        fracs = [stop_frac(r) for r in subset]
        cost = [float(ROUND_TRIP_COST / f) for f in fracs]
        return {"n": len(subset), "median_stop_distance_pct": float(100 * median(fracs)) if fracs else None,
                "mean_cost_r": sum(cost) / len(cost) if cost else None,
                "median_cost_r": median(cost) if cost else None,
                "cost_r_at_median_stop_distance": float(ROUND_TRIP_COST / median(fracs)) if fracs else None}

    def fwd(i):
        p = run.path_after(bars, i, run.PRIMARY_HORIZON)
        return float(100 * (p[-1]["close"] / bars[i]["close"] - 1)) if p else None
    tdal_moves = [(run.utc_day(d["close_time_ms"]), v) for d in pop if (v := fwd(d["bar_index"])) is not None]
    move_lo, move_hi = run.bootstrap_mean_by_day(tdal_moves)

    report = {
        "label": "POST_HOC_DESCRIPTIVE_NOT_PREREGISTERED",
        "population": {"long_eligible": len(pop), "status_counts": dict(sorted(status.items())),
                       "with_reward_reference": len(labelled), "primary_decided": len(decided)},
        "close_exit_r_16bar": ci_block(close_exit),
        "wick_rule_16bar_ambiguous_as_stop": {**ci_block(wick_amb), "ambiguous_counted_as_stop": sum(
            1 for r in rows if r["reward_first_touch"].get("status") == "LABELED"
            and r["reward_first_touch"]["wick_stop"]["result"] == "AMBIGUOUS_SAME_BAR")},
        "breaching_close_distance_r": {"n": len(stops), "mean": sum(stops) / len(stops) if stops else None,
                                       "median": median(stops) if stops else None},
        "reached_target_ratio_r": {"n": len(targets), "median": median(targets) if targets else None,
                                   "fraction_under_0_25r": (sum(t < 0.25 for t in targets) / len(targets)
                                                            if targets else None)},
        "bar1_resolved_close_rule": {"total": sum(bar1.values()), "target_first": bar1["TARGET_FIRST"],
                                     "stop_first": bar1["STOP_FIRST"]},
        "round_trip_cost": {"bps": float(ROUND_TRIP_COST * 10_000),
                            "all_long_eligible": cost_block(rows), "primary_decided": cost_block(decided),
                            "with_reward_reference": cost_block(labelled)},
        "tdal_16bar_forward_close_move_pct": {"n": len(tdal_moves),
                                              "mean": sum(v for _, v in tdal_moves) / len(tdal_moves)
                                              if tdal_moves else None,
                                              "ci95_day_bootstrap": [move_lo, move_hi]},
    }
    out_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    return report, out_rows


def main(archive_dir, registered_results, out_path):
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists; choose a new path")
    registered_bytes = Path(registered_results).read_bytes()
    if hashlib.sha256(registered_bytes).hexdigest() != REGISTERED_RESULTS_SHA256:
        raise ValueError("registered results file is not the published results_v1.json")
    a = load_archive(archive_dir, run.REGISTERED_MANIFEST_SHA256)
    bars = a["bars"]
    _, _, decisions = replay(bars, a["oi"], a["taker"], a["oi"][0]["timestamp_ms"])
    report, out_rows = compute(bars, decisions)
    registered_rows = json.loads(registered_bytes)["primary_rows"]
    if json.loads(canonical_json(out_rows)) != registered_rows:
        raise ValueError("rerun 16-bar labels differ from the registered primary_rows")
    out = {"report": report, "provenance": {
        "registered_results_sha256": REGISTERED_RESULTS_SHA256, "labels_match_registered": True,
        "manifest_sha256": a["manifest_sha256"], "code": run.git_revision()}}
    with open(out_path, "x") as f:
        f.write(canonical_json(out) + "\n")
    print(json.dumps(json.loads(canonical_json(report)), indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
