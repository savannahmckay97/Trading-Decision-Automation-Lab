"""Download Binance's public USD-M futures archive (data.binance.vision) for one symbol.

Usage: python research/historical_archive/fetch_archive.py OUT_DIR [--symbol TAOUSDT] [--start 2024-04]

Fetches, for every month from --start to the last completed day (UTC):
  klines/15m  monthly zips for completed months, daily zips for the current month
  klines/5m   same (the source of 5-minute taker buy/sell volume)
  metrics     daily zips (5-minute open interest and long/short ratios)
Every zip is verified against Binance's published .CHECKSUM (SHA-256) and kept unmodified
under OUT_DIR/raw/. A file Binance hasn't published (HTTP 404) is recorded as missing, never
synthesized. OUT_DIR/fetch_manifest.json lists every file with its URL, SHA-256 and status.
Existing verified zips are not downloaded again.
"""
import argparse
import datetime as dt
import hashlib
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um"


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tdal-research"}), timeout=60) as r:
        return r.read()


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield dt.date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def planned_files(symbol, start, last_day):
    """Yield (relative path, url) for every archive file covering start..last_day."""
    current_month = dt.date(last_day.year, last_day.month, 1)
    for interval in ("15m", "5m"):
        for month in _months(start, last_day):
            if month < current_month:
                name = f"{symbol}-{interval}-{month:%Y-%m}.zip"
                yield f"klines/{interval}/monthly/{name}", f"{BASE}/monthly/klines/{symbol}/{interval}/{name}"
            else:
                for day in range(1, last_day.day + 1):
                    name = f"{symbol}-{interval}-{month.replace(day=day):%Y-%m-%d}.zip"
                    yield f"klines/{interval}/daily/{name}", f"{BASE}/daily/klines/{symbol}/{interval}/{name}"
    day = start
    while day <= last_day:
        name = f"{symbol}-metrics-{day:%Y-%m-%d}.zip"
        yield f"metrics/{name}", f"{BASE}/daily/metrics/{symbol}/{name}"
        day += dt.timedelta(days=1)


def fetch_one(raw_dir, rel, url):
    path = raw_dir / rel
    try:
        expected = _get(url + ".CHECKSUM").decode().split()[0]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"path": rel, "url": url, "status": "MISSING_NOT_PUBLISHED"}
        raise
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        return {"path": rel, "url": url, "sha256": expected, "status": "OK"}
    body = _get(url)
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        return {"path": rel, "url": url, "sha256": actual, "expected": expected, "status": "CHECKSUM_MISMATCH"}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return {"path": rel, "url": url, "sha256": actual, "status": "OK"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--symbol", default="TAOUSDT")
    ap.add_argument("--start", default="2024-04")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out_dir)
    raw = out / "raw"
    start = dt.datetime.strptime(args.start, "%Y-%m").date()
    # Binance publishes a day's files after it ends; stop two days back to avoid partial publication.
    last_day = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=2)
    plan = list(planned_files(args.symbol, start, last_day))
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda p: fetch_one(raw, *p), plan))
    manifest = {
        "source": BASE,
        "symbol": args.symbol,
        "start": str(start),
        "last_day": str(last_day),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "files": results,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "fetch_manifest.json").write_text(json.dumps(manifest, indent=1))
    status = {}
    for r in results:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print(json.dumps(status))
    for r in results:
        if r["status"] != "OK":
            print(r["status"], r["path"])


if __name__ == "__main__":
    main()
