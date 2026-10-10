"""15m TAOUSDT klines covering the backfill window plus 16 days before it."""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path
out = Path(sys.argv[1]); meta = json.loads(Path(sys.argv[2]).read_text())
def get(**p):
    url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TradingDecisionAutomationLab/0.1"}), timeout=20) as r:
        return json.load(r)
start, end, step = meta["deriv_start_ms"] - 16 * 86_400_000, meta["server_time_ms"], 900_000
rows, cursor = {}, start
while cursor < end:
    for r in get(symbol="TAOUSDT", interval="15m", startTime=cursor, endTime=min(cursor + 999 * step, end), limit=1000):
        rows[int(r[0])] = r
    cursor += 999 * step; time.sleep(0.25)
data = [rows[k] for k in sorted(rows)]
ts = [int(r[0]) for r in data]
print(len(data), "gaps:", sum(1 for a, b in zip(ts, ts[1:]) if b - a != step))
(out / "klines.json").write_text(json.dumps(data))
