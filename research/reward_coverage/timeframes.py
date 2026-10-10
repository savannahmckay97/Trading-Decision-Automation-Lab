"""Reward-reference coverage by aggregated timeframe (availability and distance only; no outcomes).

Usage: timeframes.py <backfill_30d dir> <klines_long/klines.json>. All variants tried are listed
in docs/REWARD_REFERENCE.md section 7."""
import json, sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup_frequency"))
from history import replay
bars, *_, decisions = replay(sys.argv[1])
long = [{"open_time_ms": r[0], "high": Decimal(r[2]), "close_time_ms": r[6]} for r in json.loads(Path(sys.argv[2]).read_text())]
idx = {b["close_time_ms"]: i for i, b in enumerate(long)}
def aggregate(rows, n):
    # complete n-bar groups aligned to UTC multiples of n*15m; partial current group excluded
    out, group = [], []
    for r in rows:
        if r["open_time_ms"] % (n * 900_000) == 0: group = []
        group.append(r)
        if len(group) == n: out.append(max(x["high"] for x in group))
    return out
def nearest(highs, ref, k):
    piv = [highs[i] for i in range(k, len(highs) - k)
           if all(highs[i] > highs[j] for j in range(i - k, i)) and all(highs[i] >= highs[j] for j in range(i + 1, i + k + 1))]
    above = [h for h in piv if h > ref]
    return min(above) if above else None
for label, n, k, days in (("1h k=2", 4, 2, 7), ("4h k=2", 16, 2, 7), ("4h k=2", 16, 2, 14)):
    res = []
    for d in decisions:
        i = idx[d["close_time_ms"]]
        window = long[max(0, i + 1 - days * 96): i + 1]
        highs = aggregate(window, n)
        ref = d["risk"]["reference_price"]["value"]; atr = d["risk"]["invalidation"]["derivation"]["breakout_atr"]
        lvl = nearest(highs, ref, k)
        res.append(None if lvl is None else round(float((lvl - ref) / atr), 1))
    got = sorted(x for x in res if x is not None)
    print(f"{label} over {days}d: available {len(got)}/17; reward in ATR {got}")
