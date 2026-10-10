from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

from market_observer.config import ObserverConfig
from market_observer.dataset import DatasetError, build_dataset_artifact, write_dataset
from market_observer.ingestion import IngestionError, import_klines
from market_observer.replay import ReplayError, ReplayRunner, load_jsonl_snapshots
from market_observer.snapshots import SnapshotError, decode_snapshot


INTERVAL = 900_000
START = 1_759_276_800_000


def source(count=100):
    return {
        "schema_version": "binance_kline_source/0.1", "venue": "binance",
        "market_type": "usds_m_futures", "symbol": "TAOUSDT", "base_asset": "TAO",
        "quote_asset": "USDT", "interval": "15m", "price_unit": "USDT_per_base",
        "volume_unit": "base_asset", "start_open_time_ms": START,
        "end_open_time_ms": START + count * INTERVAL,
        "acquired_at_ms": START + (count + 5) * INTERVAL,
        "klines": [[START + i * INTERVAL, "100.000000000000000001", "102", "99", "101",
                    "10", START + (i + 1) * INTERVAL - 1, "1010", 20, "6", "606", "0"]
                   for i in range(count)],
    }


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "source.json"

    def ingest(self, envelope=None, **kwargs):
        self.path.write_text(json.dumps(source() if envelope is None else envelope), encoding="utf-8")
        return import_klines(self.path, base_asset="TAO", **kwargs)

    def test_provider_klines_map_to_exact_decimal_candles(self):
        rows, manifest = self.ingest()
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0]["candles_15m"][0]["open"], Decimal("100.000000000000000001"))
        self.assertEqual(rows[-1]["candles_15m"][-1]["volume"], Decimal("10"))
        self.assertEqual(manifest["source_metadata"]["source_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(manifest["source_metadata"]["source_identity"], "asserted_not_authenticated")

    def test_rejects_wrong_identity_interval_units_and_range(self):
        changes = {"venue": "kraken", "market_type": "spot", "symbol": "BTCUSDT",
                   "base_asset": "BTC", "quote_asset": "USD", "interval": "5m",
                   "price_unit": "USD", "volume_unit": "quote_asset",
                   "schema_version": "unknown", "start_open_time_ms": START + 1,
                   "end_open_time_ms": START, "acquired_at_ms": START,
                   "labels": [], "start_open_time_ms_bool": True}
        for key, value in changes.items():
            with self.subTest(field=key):
                item = source()
                item[key] = value
                with self.assertRaises(IngestionError):
                    self.ingest(item)
        for value in (True, "100", 1.5, -1):
            item = source()
            item["start_open_time_ms"] = value
            with self.assertRaises(IngestionError):
                self.ingest(item)

    def test_rejects_invalid_ohlcv_and_malformed_provider_rows(self):
        for field, value in ((1, "0"), (2, "99"), (3, "103"), (4, "NaN"),
                             (5, "-1"), (5, True), (5, "Infinity"), (0, True),
                             (6, START + INTERVAL), (8, 1.5), (9, "11"), (10, "1011")):
            with self.subTest(field=field, value=value):
                item = source()
                item["klines"][0][field] = value
                with self.assertRaises(IngestionError):
                    self.ingest(item)
        for row in ([], [1] * 7, [1] * 13, {"labels": []}):
            item = source()
            item["klines"][0] = row
            with self.assertRaises(IngestionError):
                self.ingest(item)

    def test_rejects_reverse_order_gaps_duplicates_and_truncated_coverage(self):
        original = source()
        variants = [list(reversed(original["klines"])), original["klines"][1:],
                    original["klines"][:-1], original["klines"][:50]+original["klines"][51:]]
        for conflicting in (False, True):
            rows = deepcopy(original["klines"])
            rows[1] = deepcopy(rows[0])
            if conflicting:
                rows[1][4] = "100"
            variants.append(rows)
        for rows in variants:
            item = deepcopy(original)
            item["klines"] = rows
            with self.assertRaises(IngestionError):
                self.ingest(item)

    def test_future_candles_do_not_change_earlier_snapshot(self):
        first, manifest = self.ingest()
        changed = source()
        changed["klines"][-1][4] = "100.5"
        second, other_manifest = self.ingest(changed)
        self.assertEqual(first[:-1], second[:-1])
        self.assertNotEqual(manifest["source_metadata"]["source_sha256"], other_manifest["source_metadata"]["source_sha256"])
        for row in first:
            self.assertTrue(all(c["close_time_ms"] < row["exchange_time_ms"] for c in row["candles_15m"]))

    def test_acquisition_time_is_not_rewritten_as_historical_receipt(self):
        rows, manifest = self.ingest()
        acquired = source()["acquired_at_ms"]
        for row in rows:
            self.assertEqual(row["observed_at_ms"], acquired)
            self.assertEqual(row["provenance"]["binance_klines"]["received_time_ms"], acquired)
            self.assertEqual(row["historical_context"]["availability"], "unverified")
        self.assertEqual(manifest["first_observed_at_ms"], manifest["last_observed_at_ms"])
        self.assertEqual(manifest["source_metadata"]["first_cutoff_ms"], START+INTERVAL)
        self.assertEqual(manifest["source_metadata"]["last_cutoff_ms"], START+100*INTERVAL)

    def test_missing_context_stays_missing_and_replay_fails_closed(self):
        rows, _ = self.ingest()
        result = ReplayRunner(ObserverConfig()).run(rows)
        self.assertEqual(result["summary"]["signal_counts"], {"DATA_STALE": 100})
        for row, decision in zip(rows, result["decisions"]):
            self.assertEqual(row["open_interest_5m"], [])
            self.assertEqual(row["taker_flow_5m"], [])
            self.assertTrue(all(value is None for value in row["market"].values()))
            self.assertEqual(decision["action"], "NO_ORDER")
            self.assertIsNone(decision["completed_candle_close_time_ms"])
        self.assertIn("open-interest history is incomplete", str(result["decisions"][-1]["gates"]))

    def test_reconstruction_cannot_hide_unverified_availability_or_future_rows(self):
        rows, _ = self.ingest()
        for change in ("flags", "availability", "receipt", "future", "quote"):
            item = deepcopy(rows[-1])
            if change == "flags": item["quality"]["critical_missing"] = []
            if change == "availability": item["historical_context"]["availability"] = "verified"
            if change == "receipt": item["historical_context"]["acquired_at_ms"] = 1
            if change == "future": item["candles_15m"][-1]["close_time_ms"] += 1
            if change == "quote": item["market"]["binance_last_price"] = Decimal("101")
            with self.subTest(change=change), self.assertRaises(SnapshotError):
                decode_snapshot(item)

    def test_reversed_cutoffs_rejected_even_with_identical_acquisition_time(self):
        rows, _ = self.ingest()
        with self.assertRaises(ReplayError):
            ReplayRunner(ObserverConfig()).run(reversed(rows))
        with self.assertRaises(DatasetError):
            build_dataset_artifact(reversed(rows), source="synthetic")

    def test_rolling_windows_are_bounded_and_repeat_import_is_deterministic(self):
        rows, manifest = self.ingest(window_size=97)
        self.assertEqual(len(rows[-1]["candles_15m"]), 97)
        self.assertEqual((rows, manifest), self.ingest(window_size=97))
        with self.assertRaises(IngestionError):
            self.ingest(window_size=0)

    def test_cli_import_to_jsonl_replay_and_immutable_outputs(self):
        self.ingest()
        data, meta, replay = (self.root/name for name in ("data.jsonl", "manifest.json", "replay.json"))
        command = [sys.executable, "-m", "market_observer.ingestion", "--source", str(self.path),
                   "--base", "TAO", "--output", str(data), "--manifest", str(meta)]
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["snapshot_count"], 100)
        original = data.read_bytes()
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(data.read_bytes(), original)
        subprocess.run([sys.executable, "-m", "market_observer.replay", "--jsonl", str(data),
                        "--base", "TAO", "--output", str(replay)], capture_output=True, check=True)
        result = json.loads(replay.read_text())
        self.assertEqual(result["summary"]["signal_counts"], {"DATA_STALE": 100})
        self.assertEqual(len(list(load_jsonl_snapshots(data))), 100)


if __name__ == "__main__":
    unittest.main()
