"""Download ~30 days of public Binance USD-M TAOUSDT data: 15m klines, 5m OI history, 5m taker ratio."""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://fapi.binance.com"
def get(path, **params):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "TradingDecisionAutomationLab/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)
now = get("/fapi/v1/time")["serverTime"]
FIVE = 300_000; DAY = 86_400_000
def paged(path, period_ms, start, key, **extra):
    rows, cursor = {}, start
    while cursor < now:
        batch = get(path, symbol="TAOUSDT", startTime=cursor, endTime=min(cursor + 499 * period_ms, now), limit=500, **extra)
        for row in batch:
            rows[int(row[key] if isinstance(row, dict) else row[0])] = row
        cursor += 499 * period_ms  # overlap one row; dict de-duplicates
        time.sleep(0.25)
    return [rows[k] for k in sorted(rows)]
# Derivative history is limited to ~30 days; stay just inside it.
deriv_start = now - 29 * DAY
klines = paged("/fapi/v1/klines", 900_000, deriv_start - 160 * 900_000, 0, interval="15m")
oi = paged("/futures/data/openInterestHist", FIVE, deriv_start, "timestamp", period="5m")
taker = paged("/futures/data/takerlongshortRatio", FIVE, deriv_start, "timestamp", period="5m")
for name, data in (("klines", klines), ("oi_hist", oi), ("taker", taker)):
    (OUT / f"{name}.json").write_text(json.dumps(data))
(OUT / "meta.json").write_text(json.dumps({"server_time_ms": now, "deriv_start_ms": deriv_start}))
print({"klines": len(klines), "oi": len(oi), "taker": len(taker), "server": now})
