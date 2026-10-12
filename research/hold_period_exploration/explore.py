"""Hold-period exploration of BREAKOUT_RETEST_V1 signals on TAO. EXPLORATORY, NOT PREREGISTERED.

Usage: python research/hold_period_exploration/explore.py ARCHIVE_DIR OUT_JSON [REGISTERED_RESULTS_JSON]

Question (from the owner): would the simulation still end at a loss if positions were held
longer or closed earlier? This script re-uses data that has already been examined (PR #21), so
nothing it prints can confirm anything; it can only suggest hypotheses for a new design that
would then have to be tested on data it was not designed on. It changes no rule or parameter:
the 543 LONG_ELIGIBLE signals come from the unchanged production replay (replay_archive.py).

Design (fixed in README.md and committed before this script was first run):
- Signals: the 543 v1 decisions whose signal candle closed in the PR #21 window
  (2024-04-12 .. 2026-09-10 UTC).
- Entry: the open of the first 15m candle opening at or after the assumed decision time,
  4 minutes after the signal candle's close (in practice the candle after next). Binance USD-M
  prices stand in for Kraken spot; costs below model the rest.
- Horizons: 1h 4h 12h 1d 3d 7d 14d 30d (4..2880 bars), counted from the entry candle. Time exit
  at the close of the last candle of the hold. A trade whose full horizon runs past the end of
  the data is censored for that horizon (counted, not guessed).
- Exit policies (each also exits at the horizon):
    a  time exit only
    b  close stop: exit at the first candle CLOSE below the v1 invalidation level
    c  wick stop at invalidation: exit when a candle's low touches it, at the level, or at the
       candle's open if it opened below (gap -> worse open)
    d1 wick stop at invalidation - 1 x ATR   (ATR = the setup's 15m breakout ATR, known at entry)
    d2 wick stop at invalidation - 2 x ATR
    e  trailing: start at invalidation - 2 x ATR; after each completed candle raise the stop to
       (highest high since entry - 10 x ATR) if that is higher; never lower it. Wick execution,
       gap -> worse open. 10 x the 15m ATR is about a one-day range if moves scale with the
       square root of time (sqrt(96) ~ 9.8). Declared before the first run; the only variant.
- Costs (Kraken Tier 4, per side, multiplicative on that side's price): fee + 5 bps slippage +
  half of a 3.35 bps spread. maker in (20) / taker out (35) = 68.35 bps round trip;
  taker in / taker out (35 / 35) = 83.35 bps. No funding (spot), no gap-through beyond the open.
- Returns in % of entry price. R = net return / (v1 stop distance as a fraction of the signal
  close), i.e. in units of the v1 rule's own risk, whatever the exit policy.
- Overlap: "every signal" treats all signals as separate trades (they overlap, so they are not
  independent); "one position at a time" takes a signal only when flat, per cell, and also
  reports the compounded account result and time in market.
- Uncertainty: 95% percentile interval of the mean, resampling UTC days (of the signal close)
  with replacement, 10,000 draws, seed 20261013, as in run.py.
- Baselines, same entries/exits/costs:
    every candle   every evaluated window candle treated as a signal, time exit only
    matched random 20,000 random window candles (seed 20261013), each given the stop distance
                   and ATR (as fractions of price) of a randomly drawn v1 signal, all policies
    buy and hold   first window open to last window close, one round trip
    drift          TAO's geometric drift per horizon over the window, and the every-candle mean
  plus a trend control: each signal's gross time-exit return minus the mean gross return of
  every candle whose close falls in the same UTC calendar month (same horizon).
"""
import datetime as dt
import hashlib
import json
import math
import random
import sys
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "historical_archive"))
import run as hist_run  # noqa: E402
from archive import load_archive  # noqa: E402
from replay_archive import replay  # noqa: E402

from market_observer.util import canonical_json  # noqa: E402

LABEL = "EXPLORATORY_ON_ALREADY_EXAMINED_DATA_NOT_PREREGISTERED_HYPOTHESIS_GENERATING_ONLY"
HORIZONS = (("1h", 4), ("4h", 16), ("12h", 48), ("1d", 96), ("3d", 288), ("7d", 672), ("14d", 1344),
            ("30d", 2880))
POLICIES = ("a_time", "b_close_stop", "c_wick_stop", "d1_wick_inv_minus_1atr", "d2_wick_inv_minus_2atr",
            "e_trail_10atr")
BAR_MS = 900_000
DECISION_DELAY_MS = 240_000
SLIPPAGE, HALF_SPREAD = 0.0005, 0.000335 / 2
COSTS = {  # (entry side, exit side), fractions of that side's price
    "maker_in_taker_out": (0.0020 + SLIPPAGE + HALF_SPREAD, 0.0035 + SLIPPAGE + HALF_SPREAD),
    "taker_in_taker_out": (0.0035 + SLIPPAGE + HALF_SPREAD, 0.0035 + SLIPPAGE + HALF_SPREAD),
}
TRAIL_INITIAL_ATR, TRAIL_ATR = 2, 10
SEED, BOOT_DRAWS, RANDOM_BASELINE_N = 20261013, 10_000, 20_000
EXPECTED_SIGNALS = 543
REGISTERED_RESULTS_SHA256 = "0b915fa6541394973b3c10ca3227db5f4d851a28bb0be50a076b1bb21cd66e37"


class Prices:
    """Float copies of the 15m series for fast path scans. Bars must be contiguous."""

    def __init__(self, bars):
        self.open_ms = [b["open_time_ms"] for b in bars]
        if any(b - a != BAR_MS for a, b in zip(self.open_ms, self.open_ms[1:])):
            raise ValueError("15m bars are not contiguous; this study assumes no gaps")
        self.o = [float(b["open"]) for b in bars]
        self.h = [float(b["high"]) for b in bars]
        self.l = [float(b["low"]) for b in bars]
        self.c = [float(b["close"]) for b in bars]
        self.n = len(bars)

    def entry_index(self, signal_index):
        """First candle opening at/after the signal close + the decision delay."""
        decision_ms = self.open_ms[signal_index] + BAR_MS + DECISION_DELAY_MS
        return bisect_left(self.open_ms, decision_ms)


def first_stop_exit(p, e, policy, inv, atr, max_bars):
    """(offset k, exit price) of the first stop exit within max_bars candles from entry e, or None."""
    last = min(e + max_bars, p.n)
    if policy == "a_time":
        return None
    if policy == "b_close_stop":
        for j in range(e, last):
            if p.c[j] < inv:
                return j - e, p.c[j]
        return None
    if policy in ("c_wick_stop", "d1_wick_inv_minus_1atr", "d2_wick_inv_minus_2atr"):
        stop = inv - {"c_wick_stop": 0, "d1_wick_inv_minus_1atr": 1, "d2_wick_inv_minus_2atr": 2}[policy] * atr
        for j in range(e, last):
            if p.o[j] <= stop:
                return j - e, p.o[j]
            if p.l[j] <= stop:
                return j - e, stop
        return None
    if policy == "e_trail_10atr":
        stop, high = inv - TRAIL_INITIAL_ATR * atr, -math.inf
        for j in range(e, last):
            if p.o[j] <= stop:
                return j - e, p.o[j]
            if p.l[j] <= stop:
                return j - e, stop
            high = max(high, p.h[j])  # completed candle: only now may the stop move
            stop = max(stop, high - TRAIL_ATR * atr)
        return None
    raise ValueError(policy)


def net_return(entry, exit_, cost):
    c_in, c_out = COSTS[cost]
    return exit_ * (1 - c_out) / (entry * (1 + c_in)) - 1


def simulate(p, e, inv, atr):
    """{(horizon_label, policy): (exit_index, gross, {cost: net}) or None if censored}."""
    out, entry = {}, p.o[e] if e < p.n else None
    max_h = HORIZONS[-1][1]
    for policy in POLICIES:
        hit = first_stop_exit(p, e, policy, inv, atr, max_h) if entry is not None else None
        for label, h in HORIZONS:
            if e + h - 1 >= p.n:
                out[(label, policy)] = None  # horizon runs past the data: censored
                continue
            if hit is not None and hit[0] < h:
                x, price = e + hit[0], hit[1]
            else:
                x, price = e + h - 1, p.c[e + h - 1]
            out[(label, policy)] = (x, price / entry - 1, {k: net_return(entry, price, k) for k in COSTS})
    return out


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def bootstrap(cases, keys, draws=BOOT_DRAWS, seed=SEED):
    """Day-cluster percentile 95% CI of the mean of each value in keys. cases: [(day, {key: v})]."""
    by_day = defaultdict(lambda: [0, defaultdict(float)])
    for day, vals in cases:
        by_day[day][0] += 1
        for k in keys:
            by_day[day][1][k] += vals[k]
    days = sorted(by_day)
    if len(days) < hist_run.MIN_BOOTSTRAP_DAYS:
        return {k: None for k in keys}
    counts = [by_day[d][0] for d in days]
    sums = {k: [by_day[d][1][k] for d in days] for k in keys}
    rng, idx = random.Random(seed), range(len(days))
    stats = {k: [] for k in keys}
    for _ in range(draws):
        picks = rng.choices(idx, k=len(days))
        n = sum(map(counts.__getitem__, picks))
        for k in keys:
            stats[k].append(sum(map(sums[k].__getitem__, picks)) / n)
    out = {}
    for k in keys:
        s = sorted(stats[k])
        out[k] = [s[int(0.025 * draws)], s[int(0.975 * draws) - 1]]
    return out


def cell_stats(trades, with_r=True):
    """trades: dicts with day, gross, net{cost}, and optionally r. Percentages, not fractions."""
    if not trades:
        return {"n": 0}
    gross = [t["gross"] for t in trades]
    out = {"n": len(trades), "days": len({t["day"] for t in trades}),
           "mean_gross_pct": 100 * sum(gross) / len(gross), "median_gross_pct": 100 * median(gross),
           "median_abs_gross_pct": 100 * median(abs(g) for g in gross)}
    keys = list(COSTS)
    ci = bootstrap([(t["day"], t["net"]) for t in trades], keys)
    for k in keys:
        net = [t["net"][k] for t in trades]
        out[f"mean_net_pct_{k}"] = 100 * sum(net) / len(net)
        out[f"win_rate_{k}"] = sum(v > 0 for v in net) / len(net)
        out[f"ci95_mean_net_pct_{k}"] = [100 * v for v in ci[k]] if ci[k] else None
    if with_r:
        rs = [t["r"] for t in trades]
        out["mean_net_r_maker_in_taker_out"] = sum(rs) / len(rs)
        out["median_net_r_maker_in_taker_out"] = median(rs)
    return out


def one_at_a_time(trades, window_bars):
    """Take a trade only when flat (previous exit candle has closed). trades sorted by entry."""
    taken, flat_from = [], -1
    for t in sorted(trades, key=lambda t: t["entry_index"]):
        if t["entry_index"] > flat_from:
            taken.append(t)
            flat_from = t["exit_index"]
    out = cell_stats(taken)
    for k in COSTS:
        growth = math.prod(1 + t["net"][k] for t in taken)
        out[f"compounded_return_pct_{k}"] = 100 * (growth - 1)
    out["time_in_market_fraction"] = sum(t["exit_index"] - t["entry_index"] + 1 for t in taken) / window_bars
    return out


def trend_summary(p, start_i, end_i):
    """TAO's own path over the window: start, end, extremes, and half/quarter returns."""
    first, last = p.o[start_i], p.c[end_i]
    lo_i = min(range(start_i, end_i + 1), key=p.l.__getitem__)
    hi_i = max(range(start_i, end_i + 1), key=p.h.__getitem__)
    periods = defaultdict(list)
    for i in range(start_i, end_i + 1):
        d = day_of(p.open_ms[i])
        periods[f"{d.year}-Q{(d.month - 1) // 3 + 1}"].append(i)
    quarters = {q: 100 * (p.c[ix[-1]] / p.o[ix[0]] - 1) for q, ix in sorted(periods.items())}
    split = bisect_left(p.open_ms, hist_run.HALF_SPLIT_MS)
    return {
        "window_first_open": first, "window_last_close": last,
        "window_return_pct": 100 * (last / first - 1),
        "low": [p.l[lo_i], str(day_of(p.open_ms[lo_i]))], "high": [p.h[hi_i], str(day_of(p.open_ms[hi_i]))],
        "h1_return_pct": 100 * (p.c[split - 1] / first - 1), "h2_return_pct": 100 * (last / p.o[split] - 1),
        "quarter_return_pct": quarters,
    }


def main(archive_dir, out_path, registered_results=None):
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists; outputs are never overwritten")
    a = load_archive(archive_dir, hist_run.REGISTERED_MANIFEST_SHA256)
    bars = a["bars"]
    _, per_candle, decisions = replay(bars, a["oi"], a["taker"], a["oi"][0]["timestamp_ms"])
    pop = [d for d in decisions if hist_run.WINDOW_START_MS <= d["close_time_ms"] < hist_run.WINDOW_END_MS]
    if len(pop) != EXPECTED_SIGNALS:
        raise ValueError(f"expected {EXPECTED_SIGNALS} v1 signals in the window, got {len(pop)}")
    if registered_results:
        raw = Path(registered_results).read_bytes()
        if hashlib.sha256(raw).hexdigest() != REGISTERED_RESULTS_SHA256:
            raise ValueError("registered results file is not the published results_v1.json")
        reg = [(r["close_time_ms"], r["setup_id"]) for r in json.loads(raw)["primary_rows"]]
        if reg != [(d["close_time_ms"], d["setup_id"]) for d in pop]:
            raise ValueError("replayed signals differ from the registered PR #21 population")
    p = Prices(bars)
    index = {b["close_time_ms"]: i for i, b in enumerate(bars)}
    window_idx = [index[c[0]] for c in per_candle if hist_run.WINDOW_START_MS <= c[0] < hist_run.WINDOW_END_MS]
    start_i = bisect_left(p.open_ms, hist_run.WINDOW_START_MS)
    end_i = bisect_left(p.open_ms, hist_run.WINDOW_END_MS) - 1
    window_bars = end_i - start_i + 1

    # Signals.
    sig_trades, sig_meta, entry_lag = defaultdict(list), [], []
    for d in pop:
        i, risk = d["bar_index"], d["risk"]
        ref = float(risk["reference_price"]["value"])
        inv = float(risk["invalidation"]["level"])
        atr = float(risk["invalidation"]["derivation"]["breakout_atr"])
        e = p.entry_index(i)
        entry_lag.append(e - i)
        dist = (ref - inv) / ref
        sig_meta.append({"stop_frac": dist, "atr_frac": atr / ref})
        day = day_of(d["close_time_ms"])
        for key, res in simulate(p, e, inv, atr).items():
            if res is not None:
                x, gross, net = res
                sig_trades[key].append({"day": day, "close_ms": d["close_time_ms"], "entry_index": e,
                                        "exit_index": x, "gross": gross, "net": net,
                                        "r": net["maker_in_taker_out"] / dist, "signal_index": i})
    if set(entry_lag) != {2}:
        raise ValueError(f"unexpected entry lags {set(entry_lag)}")

    # Every candle (time exit) and month means for the trend control.
    every = defaultdict(list)
    month_sum = defaultdict(lambda: [0.0, 0])
    for i in window_idx:
        e = p.entry_index(i)
        for label, h in HORIZONS:
            if e + h - 1 >= p.n:
                continue
            exit_ = p.c[e + h - 1]
            gross = exit_ / p.o[e] - 1
            day = day_of(p.open_ms[i] + BAR_MS)
            every[label].append({"day": day, "gross": gross, "net": {k: net_return(p.o[e], exit_, k) for k in COSTS},
                                 "entry_index": e, "exit_index": e + h - 1})
            ms = month_sum[(label, day.year, day.month)]
            ms[0] += gross
            ms[1] += 1

    # Matched random baseline.
    rng = random.Random(SEED)
    rand_trades = defaultdict(list)
    for _ in range(RANDOM_BASELINE_N):
        i = rng.choice(window_idx)
        m = rng.choice(sig_meta)
        ref = p.c[i]
        inv, atr = ref * (1 - m["stop_frac"]), ref * m["atr_frac"]
        e = p.entry_index(i)
        day = day_of(p.open_ms[i] + BAR_MS)
        for key, res in simulate(p, e, inv, atr).items():
            if res is not None:
                x, gross, net = res
                rand_trades[key].append({"day": day, "gross": gross, "net": net,
                                         "r": net["maker_in_taker_out"] / m["stop_frac"]})

    report = {"label": LABEL, "population": {"signals": len(pop), "signal_days": len({day_of(d["close_time_ms"])
                                                                                       for d in pop})},
              "costs_round_trip_bps": {k: 10_000 * (v[0] + v[1]) for k, v in COSTS.items()},
              "signal_stop_distance_pct_median": 100 * median(m["stop_frac"] for m in sig_meta),
              "signal_atr_pct_median": 100 * median(m["atr_frac"] for m in sig_meta),
              "trend": trend_summary(p, start_i, end_i), "horizons": {}}
    bh_entry, bh_exit = p.o[start_i], p.c[end_i]
    report["buy_and_hold_window"] = {"gross_pct": 100 * (bh_exit / bh_entry - 1),
                                     **{f"net_pct_{k}": 100 * net_return(bh_entry, bh_exit, k) for k in COSTS},
                                     "bars": window_bars}
    for label, h in HORIZONS:
        block = {"bars": h}
        ev = every[label]
        block["drift_geometric_pct"] = 100 * ((bh_exit / bh_entry) ** (h / window_bars) - 1)
        block["every_candle_time_exit"] = cell_stats(ev, with_r=False)
        block["cost_drag"] = {
            "round_trip_pct": {k: 100 * (v[0] + v[1]) for k, v in COSTS.items()},
            "every_candle_median_abs_move_pct": block["every_candle_time_exit"]["median_abs_gross_pct"],
            "every_candle_mean_abs_move_pct": 100 * sum(abs(t["gross"]) for t in ev) / len(ev),
            "maker_in_taker_out_cost_over_median_abs_move": 0.6835 / block["every_candle_time_exit"][
                "median_abs_gross_pct"],
        }
        block["signals_every_signal"], block["signals_one_at_a_time"], block["matched_random"] = {}, {}, {}
        block["signals_halves_mean_net_pct_maker_in_taker_out"] = {}
        for policy in POLICIES:
            trades = sig_trades[(label, policy)]
            block["signals_every_signal"][policy] = {**cell_stats(trades),
                                                     "censored": len(pop) - len(trades)}
            block["signals_one_at_a_time"][policy] = one_at_a_time(trades, window_bars)
            block["matched_random"][policy] = cell_stats(rand_trades[(label, policy)])
            halves = {}
            for name, sel in (("H1", lambda t: t["close_ms"] < hist_run.HALF_SPLIT_MS),
                              ("H2", lambda t: t["close_ms"] >= hist_run.HALF_SPLIT_MS)):
                sub = [t["net"]["maker_in_taker_out"] for t in trades if sel(t)]
                halves[name] = {"n": len(sub), "mean_net_pct": 100 * sum(sub) / len(sub) if sub else None}
            block["signals_halves_mean_net_pct_maker_in_taker_out"][policy] = halves
        # Trend control: signal gross time-exit return minus same-month every-candle mean.
        ex = []
        for t in sig_trades[(label, "a_time")]:
            d = day_of(t["close_ms"])
            s, n = month_sum[(label, d.year, d.month)]
            ex.append((t["day"], {"x": t["gross"] - s / n}))
        ci = bootstrap(ex, ["x"])
        block["signal_minus_same_month_every_candle_gross_pct"] = {
            "n": len(ex), "mean": 100 * sum(v["x"] for _, v in ex) / len(ex),
            "ci95": [100 * v for v in ci["x"]] if ci["x"] else None}
        report["horizons"][label] = block

    report["provenance"] = {"manifest_sha256": a["manifest_sha256"], "code": hist_run.git_revision(),
                            "registered_population_checked": bool(registered_results),
                            "seed": SEED, "bootstrap_draws": BOOT_DRAWS, "random_baseline_n": RANDOM_BASELINE_N}
    with open(out_path, "x") as f:
        f.write(canonical_json(report) + "\n")
    print_tables(report)
    return report


def _f(v, nd=2, signed=True):
    if v is None:
        return "n/a"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def _ci(v):
    return "n/a" if not v else f"{v[0]:+.2f} to {v[1]:+.2f}"


def print_tables(report):
    print(f"# {report['label']}\n")
    print("## Trend\n", json.dumps(report["trend"], indent=1), "\n", json.dumps(report["buy_and_hold_window"]))
    print("\n## Baselines and cost drag per horizon\n")
    print("| Horizon | Drift (geom) % | Every-candle mean gross % | median gross % | mean net % (M/T) "
          "| 95% CI | win % | median abs move % | cost / median abs move |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, b in report["horizons"].items():
        ev = b["every_candle_time_exit"]
        print(f"| {label} | {_f(b['drift_geometric_pct'], 3)} | {_f(ev['mean_gross_pct'], 3)} | "
              f"{_f(ev['median_gross_pct'], 3)} | {_f(ev['mean_net_pct_maker_in_taker_out'])} | "
              f"{_ci(ev['ci95_mean_net_pct_maker_in_taker_out'])} | {100 * ev['win_rate_maker_in_taker_out']:.0f} | "
              f"{_f(ev['median_abs_gross_pct'], 2, False)} | "
              f"{_f(b['cost_drag']['maker_in_taker_out_cost_over_median_abs_move'], 2, False)} |")
    for version in ("signals_every_signal", "signals_one_at_a_time", "matched_random"):
        print(f"\n## {version}\n")
        extra = " compounded % (M/T) | time in mkt |" if version == "signals_one_at_a_time" else ""
        print("| Horizon | Policy | n | mean gross % | median gross % | mean net % M/T | 95% CI M/T | "
              "mean net % T/T | 95% CI T/T | win % M/T | mean R | median R |" + extra)
        print("|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if extra else ""))
        for label, b in report["horizons"].items():
            for policy, s in b[version].items():
                row = (f"| {label} | {policy} | {s['n']} | {_f(s['mean_gross_pct'])} | {_f(s['median_gross_pct'])} | "
                       f"{_f(s['mean_net_pct_maker_in_taker_out'])} | {_ci(s['ci95_mean_net_pct_maker_in_taker_out'])} | "
                       f"{_f(s['mean_net_pct_taker_in_taker_out'])} | {_ci(s['ci95_mean_net_pct_taker_in_taker_out'])} | "
                       f"{100 * s['win_rate_maker_in_taker_out']:.0f} | {_f(s['mean_net_r_maker_in_taker_out'])} | "
                       f"{_f(s['median_net_r_maker_in_taker_out'])} |")
                if extra:
                    row += (f" {_f(s['compounded_return_pct_maker_in_taker_out'], 1)} / "
                            f"{_f(s['compounded_return_pct_taker_in_taker_out'], 1)} | "
                            f"{100 * s['time_in_market_fraction']:.0f}% |")
                print(row)
    print("\n## Signal minus same-month every-candle (gross, time exit) and halves (net M/T)\n")
    for label, b in report["horizons"].items():
        x = b["signal_minus_same_month_every_candle_gross_pct"]
        hv = b["signals_halves_mean_net_pct_maker_in_taker_out"]
        halves = "; ".join(f"{pol}: H1 {_f(v['H1']['mean_net_pct'])} (n {v['H1']['n']}), "
                           f"H2 {_f(v['H2']['mean_net_pct'])} (n {v['H2']['n']})" for pol, v in hv.items())
        print(f"- {label}: excess {_f(x['mean'], 3)}% CI {_ci(x['ci95'])} (n {x['n']}) | {halves}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
