"""Reproducible archive-vs-live-API parity check over their overlap (FIDELITY.md).

Usage: python research/historical_archive/fidelity_check.py ARCHIVE_DIR BACKFILL_DIR

BACKFILL_DIR is a live-API capture in research/setup_frequency/history.py's format
(meta.json, klines.json, oi_hist.json, taker.json). Prints, for the overlapping points:
15m klines identical; open interest identical as archived and after the +5 min relabel;
taker buy, sell and both identical; and whether replay_archive.replay gives the same
state and signal per candle on API inputs and archive inputs. Reads no outcome.

This checks only the overlap. Outside it, the +5 min relabel and the 5m-kline taker
source are an extrapolated reconstruction assumption.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup_frequency"))
from archive import load_archive  # noqa: E402
from history import load as load_api  # noqa: E402
from replay_archive import replay  # noqa: E402

RUN_MANIFEST = "bfc2ae4a9968c4872650877d8949e973327b7c8c1e91601ef1b78c55923da00c"


def main(archive_dir, backfill_dir):
    a = load_archive(archive_dir, RUN_MANIFEST)
    meta, api_bars, api_oi, api_taker = load_api(backfill_dir)
    out = {}

    arch_bars = {b["open_time_ms"]: b for b in a["bars"]}
    shared = [b for b in api_bars if b["open_time_ms"] in arch_bars]
    out["klines_15m"] = {"overlap": len(shared),
                         "identical": sum(b == arch_bars[b["open_time_ms"]] for b in shared)}

    arch_oi = {r["timestamp_ms"]: r["open_interest"] for r in a["oi"]}  # already relabelled +5 min
    raw_oi = {ts - 300_000: v for ts, v in arch_oi.items()}  # as the archive labels it
    shared_oi = [r for r in api_oi if r["timestamp_ms"] in arch_oi]
    out["open_interest"] = {
        "overlap_after_relabel": len(shared_oi),
        "identical_after_plus_5min_relabel": sum(arch_oi[r["timestamp_ms"]] == r["open_interest"] for r in shared_oi),
        "identical_without_relabel": sum(raw_oi.get(r["timestamp_ms"]) == r["open_interest"] for r in api_oi),
    }

    shared_t = [(t, a["taker"][ts]) for ts, t in api_taker.items() if ts in a["taker"]]
    buy = [t["buy_volume"] == x["buy_volume"] for t, x in shared_t]
    sell = [t["sell_volume"] == x["sell_volume"] for t, x in shared_t]
    diffs = sorted(abs((x["buy_volume"] + x["sell_volume"]) - (t["buy_volume"] + t["sell_volume"]))
                   / (x["buy_volume"] + x["sell_volume"]) for t, x in shared_t
                   if (x["buy_volume"] + x["sell_volume"]) and not (t["buy_volume"] == x["buy_volume"]
                                                                  and t["sell_volume"] == x["sell_volume"]))
    out["taker_5m"] = {
        "overlap": len(shared_t), "buy_identical": sum(buy), "sell_identical": sum(sell),
        "both_identical": sum(b and s for b, s in zip(buy, sell)),
        "total_volume_rel_diff_where_not_identical": {
            "n": len(diffs), "median": float(diffs[len(diffs) // 2]) if diffs else None,
            "max": float(diffs[-1]) if diffs else None},
    }

    start = meta["deriv_start_ms"]
    _, api_pc, api_dec = replay(api_bars, api_oi, api_taker, start)
    arch_input = [arch_bars[b["open_time_ms"]] if b["open_time_ms"] in arch_bars else b for b in api_bars]
    _, arch_pc, arch_dec = replay(arch_input, a["oi"], a["taker"], start)
    api_states = {c[0]: c[1:3] for c in api_pc}
    arch_states = {c[0]: c[1:3] for c in arch_pc}
    common = sorted(set(api_states) & set(arch_states))
    out["decision_parity"] = {
        "candles_compared": len(common),
        "state_and_signal_identical": sum(api_states[t] == arch_states[t] for t in common),
        "long_eligible_api": len(api_dec), "long_eligible_archive": len(arch_dec),
        "same_long_eligible_candles": [d["close_time_ms"] for d in api_dec] == [d["close_time_ms"] for d in arch_dec],
    }
    print(json.dumps(out, indent=1, default=lambda v: str(v) if isinstance(v, Decimal) else v))


if __name__ == "__main__":
    main(*sys.argv[1:3])
