from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from market_observer.dataset import DatasetError, build_dataset_artifact, write_dataset


def snapshot(observed, exchange=None, research="TAOUSDT", executable="TAOUSD"):
    return {
        "schema_version": "normalized_snapshot/0.1",
        "observed_at_ms": observed,
        "exchange_time_ms": observed if exchange is None else exchange,
        "research_symbol": research,
        "executable_pair": executable,
        "candles_15m": [],
    }


class DatasetArtifactTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_performance_claims_remain_forbidden(self):
        rows, first = build_dataset_artifact(
            [snapshot(1000), snapshot(2000)], source="fixture"
        )
        _, second = build_dataset_artifact(rows, source="fixture")
        self.assertEqual(first, second)
        self.assertEqual(first["snapshot_count"], 2)
        self.assertFalse(first["claims"]["outcome_labels_present"])
        self.assertFalse(first["claims"]["fill_simulation_present"])
        self.assertFalse(first["claims"]["performance_claim_available"])

    def test_rejects_reverse_chronology_and_future_exchange_time(self):
        with self.assertRaises(DatasetError):
            build_dataset_artifact(
                [snapshot(2000), snapshot(1000)], source="fixture"
            )
        with self.assertRaises(DatasetError):
            build_dataset_artifact([snapshot(1000, exchange=1001)], source="fixture")

    def test_rejects_mixed_instrument_identity(self):
        with self.assertRaises(DatasetError):
            build_dataset_artifact(
                [
                    snapshot(1000),
                    snapshot(2000, research="BTCUSDT", executable="BTCUSD"),
                ],
                source="fixture",
            )

    def test_write_is_immutable_and_hash_verified(self):
        rows, manifest = build_dataset_artifact(
            [snapshot(1000), snapshot(2000)], source="fixture"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "dataset.jsonl"
            meta = root / "dataset.manifest.json"
            write_dataset(rows, manifest, jsonl_path=data, manifest_path=meta)
            self.assertEqual(
                len(data.read_text(encoding="utf-8").splitlines()), 2
            )
            self.assertEqual(
                json.loads(meta.read_text(encoding="utf-8"))["dataset_id"],
                manifest["dataset_id"],
            )
            with self.assertRaises(FileExistsError):
                write_dataset(rows, manifest, jsonl_path=data, manifest_path=meta)

    def test_write_rejects_rows_that_do_not_match_manifest_hash(self):
        rows, manifest = build_dataset_artifact([snapshot(1000)], source="fixture")
        rows.append(snapshot(2000))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DatasetError):
                write_dataset(
                    rows,
                    manifest,
                    jsonl_path=Path(directory) / "x.jsonl",
                    manifest_path=Path(directory) / "x.json",
                )


if __name__ == "__main__":
    unittest.main()
