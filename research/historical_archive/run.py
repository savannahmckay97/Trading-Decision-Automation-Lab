"""Run the frozen historical analysis (PREREGISTRATION.md, frozen at f166d3b).

Usage: python research/historical_archive/run.py ARCHIVE_DIR OUT_JSON

Uses the production rule (replay_archive.py) and the harness's _first_touch and
_first_touch_summary unchanged. Writes every per-decision label to OUT_JSON and prints
the summary. Nothing here selects or tunes anything.

Added after the registered run (review of PR #21, A1-A3); none of it changes OUT_JSON's
bytes on the registered archive:
- The archive must match the registered manifest fingerprint, and every file is re-hashed
  against the manifest before loading (archive.py).
- OUT_JSON and OUT_JSON.provenance.json are created exclusively: an existing file is an
  error, never overwritten. The provenance file records the input fingerprint, the code
  revision, the replay's skip/reset counts and coverage, and how to read the primary label.
- Empty or insufficient samples give null estimates and an INCONCLUSIVE reading with a
  reason instead of crashing.
"""
import datetime as dt
import json
import math
import platform
import random
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive import load_archive, sha256_file  # noqa: E402
from replay_archive import replay  # noqa: E402

from market_observer.evaluation import _first_touch, _first_touch_summary  # noqa: E402
from market_observer.util import canonical_json  # noqa: E402

WINDOW_START_MS = int(dt.datetime(2024, 4, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)
WINDOW_END_MS = int(dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc).timestamp() * 1000)  # exclusive
HALF_SPLIT_MS = int(dt.datetime(2025, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
HORIZONS = (16, 4, 96)  # primary first
PRIMARY_HORIZON, PRIMARY_RULE = 16, "close_stop"
BOOTSTRAP_DRAWS, SEED = 10_000, 20261013
# SHA-256 of fetch_manifest.json registered in PREREGISTRATION.md section 2.
REGISTERED_MANIFEST_SHA256 = "bfc2ae4a9968c4872650877d8949e973327b7c8c1e91601ef1b78c55923da00c"
# Added after the run (review A3): fewer UTC days than this gives no interval and an
# INCONCLUSIVE reading, so one populated day can't produce a collapsed "significant" interval.
# The registered primary has 288 days, so this never binds on it.
MIN_BOOTSTRAP_DAYS = 10


def utc_day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def path_after(bars, i, horizon):
    path = bars[i + 1: i + 1 + horizon]
    complete = len(path) == horizon and all(
        b["open_time_ms"] == bars[i]["open_time_ms"] + (k + 1) * 900_000 for k, b in enumerate(path))
    return path if complete else None


def realised_r(touch, rule):
    """+r for TARGET_FIRST, -1 for STOP_FIRST; None if not decided."""
    if touch.get("status") != "LABELED":
        return None
    result = touch[rule]["result"]
    if result == "TARGET_FIRST":
        return Decimal(touch["gross_opportunity_to_risk"])
    if result == "STOP_FIRST":
        return Decimal(-1)
    return None


def close_exit_r(touch, decision, path):
    """Close rule, but a stop exits at the actual breaching close rather than at -1R."""
    if touch.get("status") != "LABELED":
        return None
    result, bars_to = touch["close_stop"]["result"], touch["close_stop"]["bars"]
    if result == "TARGET_FIRST":
        return Decimal(touch["gross_opportunity_to_risk"])
    if result == "STOP_FIRST":
        risk = decision["risk"]
        ref = Decimal(risk["reference_price"]["value"])
        dist = Decimal(risk["per_unit_risk"]["distance"])
        return (path[bars_to - 1]["close"] - ref) / dist
    return None


def bootstrap_mean_by_day(cases, draws=BOOTSTRAP_DRAWS, seed=SEED):
    """95% percentile CI of mean R per decided case, resampling UTC days with replacement.

    Returns (None, None) when there are no cases.
    """
    if not cases:
        return None, None
    by_day = defaultdict(list)
    for day, r in cases:
        by_day[day].append(float(r))
    days = sorted(by_day)
    sums = [sum(by_day[d]) for d in days]
    counts = [len(by_day[d]) for d in days]
    rng = random.Random(seed)
    stats = []
    for _ in range(draws):
        picks = [rng.randrange(len(days)) for _ in days]
        n = sum(counts[p] for p in picks)
        stats.append(sum(sums[p] for p in picks) / n)
    stats.sort()
    return stats[int(0.025 * draws)], stats[int(0.975 * draws) - 1]


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def summarise_r(values):
    vals = [float(v) for v in values]
    return {"n": len(vals), "mean_r": sum(vals) / len(vals) if vals else None,
            "sum_r": sum(vals) if vals else None}


def primary_summary(cases, ratios):
    """The registered primary block. cases: [(utc_day, R)] decided; ratios: their gross ratios."""
    days = len({day for day, _ in cases})
    out = {"horizon_bars": PRIMARY_HORIZON, "stop_rule": PRIMARY_RULE, "decided": len(cases), "decided_days": days}
    if not cases:
        reason = "NO_DECIDED_CASES"
    elif days < MIN_BOOTSTRAP_DAYS:
        reason = f"FEWER_THAN_{MIN_BOOTSTRAP_DAYS}_DECIDED_DAYS"
    else:
        reason = None
    lo, hi = bootstrap_mean_by_day(cases) if reason is None else (None, None)
    mean_r = sum(float(v) for _, v in cases) / len(cases) if cases else None
    if reason is None:
        reading = "GROSS_EDGE_IN_PAST" if lo > 0 else "EVIDENCE_AGAINST_RULE" if hi < 0 else "INCONCLUSIVE"
    else:
        reading = "INCONCLUSIVE"
    targets = sum(1 for _, v in cases if v != -1)
    out.update({
        "mean_gross_r": mean_r, "ci95_day_bootstrap": [lo, hi], "reading": reading,
        "target_first": targets, "target_first_fraction": targets / len(cases) if cases else None,
        "target_first_wilson95": wilson(targets, len(cases)),
        "breakeven_rate_at_mean_ratio": float(1 / (1 + sum(ratios) / len(ratios))) if ratios else None,
        "median_ratio_of_decided": float(median(ratios)) if ratios else None,
    })
    if reason is not None:
        out["reading_reason"] = reason  # only present when the estimate is unavailable
    return out


def move_stats(moves):
    """Forward-move summary; nulls and a reason for an empty sample."""
    m = [x for x in moves if x is not None]
    if not m:
        return {"n": 0, "mean_pct": None, "median_pct": None, "unavailable_reason": "NO_FORWARD_MOVES"}
    return {"n": len(m), "mean_pct": sum(m) / len(m), "median_pct": median(m)}


def git_revision():
    root = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "dirty": None}
    return {"head": head, "dirty": dirty}


def main(archive_dir, out_path, expected_manifest_sha256=REGISTERED_MANIFEST_SHA256):
    out_path = Path(out_path)
    prov_path = out_path.with_name(out_path.name + ".provenance.json")
    for p in (out_path, prov_path):
        if p.exists():
            raise FileExistsError(f"{p} exists; results are never overwritten, choose a new path")
    a = load_archive(archive_dir, expected_manifest_sha256)
    bars = a["bars"]
    if not a["oi"]:
        raise ValueError("archive has no open-interest points")
    counts, per_candle, decisions = replay(bars, a["oi"], a["taker"], a["oi"][0]["timestamp_ms"])
    pop = [d for d in decisions if WINDOW_START_MS <= d["close_time_ms"] < WINDOW_END_MS]

    report = {"population": len(pop), "population_days": len({utc_day(d["close_time_ms"]) for d in pop}),
              "horizons": {}}
    rows_by_h = {}
    for h in HORIZONS:
        rows = []
        for d in pop:
            path = path_after(bars, d["bar_index"], h)
            label = {"status": "AVAILABLE"} if path else {"status": "CENSORED", "reason": "path_beyond_data"}
            touch = _first_touch(d["risk"], label, path)
            rows.append({"close_time_ms": d["close_time_ms"], "setup_id": d["setup_id"],
                         "reward_first_touch": touch, "_path": path, "_decision": d})
        rows_by_h[h] = rows
        report["horizons"][str(h)] = {"summary": _first_touch_summary(rows)}

    # Primary: 16-bar close rule, mean realised gross R per decided case, day bootstrap.
    prim = rows_by_h[PRIMARY_HORIZON]
    cases = [(utc_day(r["close_time_ms"]), realised_r(r["reward_first_touch"], PRIMARY_RULE)) for r in prim]
    cases = [(day, v) for day, v in cases if v is not None]
    ratios = [Decimal(r["reward_first_touch"]["gross_opportunity_to_risk"]) for r in prim
              if realised_r(r["reward_first_touch"], PRIMARY_RULE) is not None]
    report["primary"] = primary_summary(cases, ratios)

    # Secondary: close-exit R and bar-1 sensitivity (primary horizon).
    ce = [close_exit_r(r["reward_first_touch"], r["_decision"], r["_path"]) for r in prim]
    report["close_exit_r"] = summarise_r([v for v in ce if v is not None])
    no_bar1 = [(day, v) for (day, v), r in zip(
        [(utc_day(r["close_time_ms"]), realised_r(r["reward_first_touch"], PRIMARY_RULE)) for r in prim], prim)
        if v is not None and r["reward_first_touch"]["close_stop"]["bars"] != 1]
    report["bar1_excluded"] = summarise_r([v for _, v in no_bar1])
    report["other_cells"] = {
        f"{h}_{rule}": summarise_r([v for v in (realised_r(r["reward_first_touch"], rule) for r in rows_by_h[h])
                                    if v is not None])
        for h in HORIZONS for rule in ("close_stop", "wick_stop")}

    # Stability: halves and calendar quarters (primary cell).
    def period_of(ms, quarters):
        d = utc_day(ms)
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}" if quarters else ("H1" if ms < HALF_SPLIT_MS else "H2")
    for key, quarters in (("halves", False), ("quarters", True)):
        groups = defaultdict(list)
        for r in prim:
            v = realised_r(r["reward_first_touch"], PRIMARY_RULE)
            if v is not None:
                groups[period_of(r["close_time_ms"], quarters)].append(v)
        report[key] = {k: summarise_r(v) for k, v in sorted(groups.items())}

    # Baselines: forward 16-bar close move from every evaluated candle in the window.
    index = {b["close_time_ms"]: i for i, b in enumerate(bars)}
    def fwd(i, h=PRIMARY_HORIZON):
        p = path_after(bars, i, h)
        return float(100 * (p[-1]["close"] / bars[i]["close"] - 1)) if p else None
    window_idx = [index[c[0]] for c in per_candle if WINDOW_START_MS <= c[0] < WINDOW_END_MS]
    report["baselines_16bar_forward_close_move"] = {
        "TDAL_LONG_ELIGIBLE": move_stats(fwd(d["bar_index"]) for d in pop),
        "SELECT_ALL": move_stats(fwd(i) for i in window_idx),
        "POSITIVE_PRIOR_RETURN": move_stats(
            fwd(i) for i in window_idx if i > 0 and bars[i]["close"] > bars[i - 1]["close"]),
    }

    out_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in prim]
    with open(out_path, "x") as f:
        f.write(canonical_json({"report": report, "primary_rows": out_rows}) + "\n")
    window_candles = [c for c in per_candle if WINDOW_START_MS <= c[0] < WINDOW_END_MS]
    provenance = {
        "result_file": out_path.name,
        "result_sha256": sha256_file(out_path),
        "input": {
            "archive_dir": str(Path(archive_dir).resolve()),
            "manifest_sha256": a["manifest_sha256"],
            "registered_manifest_sha256": REGISTERED_MANIFEST_SHA256,
            "manifest_matches_registered": a["manifest_sha256"] == REGISTERED_MANIFEST_SHA256,
            "verified_ok_files": sum(1 for f in a["manifest"]["files"] if f["status"] == "OK"),
            "missing_not_published_files": sum(
                1 for f in a["manifest"]["files"] if f["status"] == "MISSING_NOT_PUBLISHED"),
        },
        "code": {"git": git_revision(), "python": platform.python_version()},
        "configuration": {
            "window_close_ms": [WINDOW_START_MS, WINDOW_END_MS], "horizons": list(HORIZONS),
            "primary": [PRIMARY_HORIZON, PRIMARY_RULE], "bootstrap_draws": BOOTSTRAP_DRAWS, "seed": SEED,
            "min_bootstrap_days": MIN_BOOTSTRAP_DAYS,
        },
        "coverage": {
            "bars_15m": len(bars),
            "first_bar_open_ms": bars[0]["open_time_ms"] if bars else None,
            "last_bar_open_ms": bars[-1]["open_time_ms"] if bars else None,
            "oi_points": len(a["oi"]), "taker_buckets_5m": len(a["taker"]),
            "evaluated_candles_all": len(per_candle), "evaluated_candles_in_window": len(window_candles),
            "long_eligible_all": len(decisions), "long_eligible_in_window": len(pop),
        },
        "replay_counts_all_candles": dict(sorted(counts.items())),
        "interpretation": (
            "primary.reading is the registered label. The primary is a conditional, descriptive "
            "first-touch statistic over resolved cases with every close-rule loss scored -1R; it is "
            "not full-population gross expectancy or an executable trading result. See RESULTS.md."),
    }
    with open(prov_path, "x") as f:
        f.write(canonical_json(provenance) + "\n")
    print(json.dumps(json.loads(canonical_json(report)), indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
