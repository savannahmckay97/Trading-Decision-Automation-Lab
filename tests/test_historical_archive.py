from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "historical_archive"))
import archive as hist_archive  # noqa: E402
import posthoc as hist_posthoc  # noqa: E402
import run as hist_run  # noqa: E402

SYMBOL = "TAOUSDT"
START_MS = int(dt.datetime(2024, 5, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _zip_bytes(name, rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name.replace(".zip", ".csv"), "\n".join(",".join(map(str, r)) for r in rows) + "\n")
    return buf.getvalue()


def _kline(open_ms, minutes, price, buy):
    close = price + (0.1 if (open_ms // 60_000) % 2 else -0.1)
    return [open_ms, price, price + 0.5, price - 0.5, close, 10, open_ms + minutes * 60_000 - 1, 1000, 5, buy, 500, 0]


def build_archive(root, n_bars=160):
    """A small archive in fetch_archive.py's layout: one file per series plus a manifest."""
    root = Path(root)
    bars = [_kline(START_MS + i * 900_000, 15, 100.0, 5) for i in range(n_bars)]
    fives = [_kline(START_MS + i * 300_000, 5, 100.0, 5) for i in range(3 * n_bars)]
    metrics = [[dt.datetime.fromtimestamp((START_MS + i * 300_000) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                SYMBOL, 1000 + i % 7, 100000, 1, 1, 1, 1] for i in range(3 * n_bars)]
    files = {
        f"klines/15m/monthly/{SYMBOL}-15m-2024-05.zip": bars,
        f"klines/5m/monthly/{SYMBOL}-5m-2024-05.zip": fives,
        f"metrics/{SYMBOL}-metrics-2024-05-01.zip": metrics,
    }
    entries = []
    for rel, rows in files.items():
        path = root / "raw" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = _zip_bytes(path.name, rows)
        path.write_bytes(body)
        entries.append({"path": rel, "url": "synthetic", "sha256": hashlib.sha256(body).hexdigest(), "status": "OK"})
    entries.append({"path": f"metrics/{SYMBOL}-metrics-2024-04-30.zip", "url": "synthetic",
                    "status": "MISSING_NOT_PUBLISHED"})
    (root / "fetch_manifest.json").write_text(json.dumps({"symbol": SYMBOL, "files": entries}))
    return root


class ArchiveEnforcementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = build_archive(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_archive_loads(self):
        a = hist_archive.load_archive(self.root)
        self.assertEqual(len(a["bars"]), 160)
        self.assertEqual(len(a["taker"]), 480)
        self.assertEqual(a["oi"][0]["timestamp_ms"], START_MS + 300_000)  # +5 min relabel
        sha = hashlib.sha256((self.root / "fetch_manifest.json").read_bytes()).hexdigest()
        self.assertEqual(a["manifest_sha256"], sha)
        hist_archive.load_archive(self.root, sha)

    def test_manifest_fingerprint_mismatch_rejected(self):
        with self.assertRaisesRegex(hist_archive.ArchiveError, "registered"):
            hist_archive.load_archive(self.root, "0" * 64)

    def test_tampered_zip_rejected(self):
        path = self.root / "raw" / "klines/15m/monthly" / f"{SYMBOL}-15m-2024-05.zip"
        rows = [_kline(START_MS, 15, 200.0, 5)]  # a changed close, manifest SHA not updated
        path.write_bytes(_zip_bytes(path.name, rows))
        with self.assertRaisesRegex(hist_archive.ArchiveError, "SHA-256"):
            hist_archive.load_archive(self.root)

    def test_unlisted_zip_rejected(self):
        extra = self.root / "raw" / "klines/15m/daily" / f"{SYMBOL}-15m-2024-06-01.zip"
        extra.parent.mkdir(parents=True)
        extra.write_bytes(_zip_bytes(extra.name, [_kline(START_MS + 10**9, 15, 100.0, 5)]))
        with self.assertRaisesRegex(hist_archive.ArchiveError, "not listed"):
            hist_archive.load_archive(self.root)

    def test_missing_ok_file_rejected(self):
        (self.root / "raw" / "metrics" / f"{SYMBOL}-metrics-2024-05-01.zip").unlink()
        with self.assertRaisesRegex(hist_archive.ArchiveError, "missing"):
            hist_archive.load_archive(self.root)

    def test_wrong_symbol_rejected(self):
        manifest = json.loads((self.root / "fetch_manifest.json").read_text())
        manifest["symbol"] = "BTCUSDT"
        (self.root / "fetch_manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(hist_archive.ArchiveError, "symbol"):
            hist_archive.load_archive(self.root)

    def test_unverified_status_rejected(self):
        manifest = json.loads((self.root / "fetch_manifest.json").read_text())
        manifest["files"][0]["status"] = "CHECKSUM_MISMATCH"
        (self.root / "fetch_manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(hist_archive.ArchiveError, "unverified"):
            hist_archive.load_archive(self.root)


class RunOutputTest(unittest.TestCase):
    def test_empty_population_is_deterministic_and_outputs_are_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_archive(Path(tmp) / "archive")
            out = Path(tmp) / "results.json"
            with redirect_stdout(io.StringIO()):
                hist_run.main(root, out, expected_manifest_sha256=None)
            first = out.read_bytes()
            report = json.loads(first)["report"]
            self.assertEqual(report["population"], 0)
            self.assertEqual(report["primary"]["reading"], "INCONCLUSIVE")
            self.assertEqual(report["primary"]["reading_reason"], "NO_DECIDED_CASES")
            self.assertIsNone(report["primary"]["mean_gross_r"])
            self.assertEqual(report["primary"]["ci95_day_bootstrap"], [None, None])
            self.assertEqual(report["baselines_16bar_forward_close_move"]["TDAL_LONG_ELIGIBLE"]["n"], 0)
            prov = json.loads(Path(str(out) + ".provenance.json").read_text())
            self.assertEqual(prov["result_sha256"], hashlib.sha256(first).hexdigest())
            self.assertFalse(prov["input"]["manifest_matches_registered"])
            self.assertIn("head", prov["code"]["git"])

            with self.assertRaises(FileExistsError):
                hist_run.main(root, out, expected_manifest_sha256=None)
            self.assertEqual(out.read_bytes(), first)  # existing result preserved

            # Deterministic: a second run to a new path gives identical bytes.
            out2 = Path(tmp) / "results2.json"
            with redirect_stdout(io.StringIO()):
                hist_run.main(root, out2, expected_manifest_sha256=None)
            self.assertEqual(out2.read_bytes(), first)

    def test_existing_provenance_blocks_run_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_archive(Path(tmp) / "archive")
            out = Path(tmp) / "results.json"
            Path(str(out) + ".provenance.json").write_text("keep")
            with self.assertRaises(FileExistsError):
                hist_run.main(root, out, expected_manifest_sha256=None)
            self.assertFalse(out.exists())

    def test_registered_fingerprint_enforced_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_archive(Path(tmp) / "archive")
            with self.assertRaises(hist_archive.ArchiveError):
                hist_run.main(root, Path(tmp) / "results.json")


class EmptySampleTest(unittest.TestCase):
    def test_bootstrap_empty(self):
        self.assertEqual(hist_run.bootstrap_mean_by_day([]), (None, None))

    def test_primary_no_decided_cases(self):
        p = hist_run.primary_summary([], [])
        self.assertEqual((p["decided"], p["decided_days"], p["target_first"]), (0, 0, 0))
        self.assertEqual(p["reading"], "INCONCLUSIVE")
        self.assertEqual(p["reading_reason"], "NO_DECIDED_CASES")
        for key in ("mean_gross_r", "target_first_fraction", "target_first_wilson95",
                    "breakeven_rate_at_mean_ratio", "median_ratio_of_decided"):
            self.assertIsNone(p[key])

    def test_primary_too_few_days_is_inconclusive(self):
        from decimal import Decimal
        day = dt.date(2025, 1, 1)
        cases = [(day, Decimal("0.5"))] * 30  # all positive, one day: interval would collapse above 0
        p = hist_run.primary_summary(cases, [Decimal("0.5")] * 30)
        self.assertEqual(p["reading"], "INCONCLUSIVE")
        self.assertEqual(p["reading_reason"], f"FEWER_THAN_{hist_run.MIN_BOOTSTRAP_DAYS}_DECIDED_DAYS")
        self.assertEqual(p["ci95_day_bootstrap"], [None, None])
        self.assertEqual(p["mean_gross_r"], 0.5)

    def test_primary_enough_days_has_no_reason_key(self):
        from decimal import Decimal
        cases = [(dt.date(2025, 1, 1) + dt.timedelta(days=k), Decimal("0.5")) for k in range(12)]
        p = hist_run.primary_summary(cases, [Decimal("0.5")] * 12)
        self.assertEqual(p["reading"], "GROSS_EDGE_IN_PAST")
        self.assertNotIn("reading_reason", p)

    def test_move_stats_empty(self):
        self.assertEqual(hist_run.move_stats([None, None]),
                         {"n": 0, "mean_pct": None, "median_pct": None, "unavailable_reason": "NO_FORWARD_MOVES"})
        self.assertEqual(hist_run.move_stats([1.0, 3.0]), {"n": 2, "mean_pct": 2.0, "median_pct": 2.0})

    def test_posthoc_empty_population(self):
        report, rows = hist_posthoc.compute([], [])
        self.assertEqual(rows, [])
        self.assertEqual(report["label"], "POST_HOC_DESCRIPTIVE_NOT_PREREGISTERED")
        self.assertEqual(report["close_exit_r_16bar"]["ci95_day_bootstrap"], [None, None])
        self.assertIsNone(report["round_trip_cost"]["all_long_eligible"]["mean_cost_r"])


if __name__ == "__main__":
    unittest.main()
