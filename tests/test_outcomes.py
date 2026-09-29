from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market_observer.outcomes import (
    OutcomeLabelError,
    build_outcome_artifact,
    write_outcome_artifact,
)


INTERVAL = 900_000


def candle(open_time, open_, high, low, close):
    return {
        "open_time_ms": open_time,
        "close_time_ms": open_time + INTERVAL - 1,
        "open": Decimal(str(open_)),
        "high": Decimal(str(high)),
        "low": Decimal(str(low)),
        "close": Decimal(str(close)),
        "volume": Decimal("1"),
    }


def snapshot(observed, exchange, candles):
    return {
        "schema_version": "normalized_snapshot/0.1",
        "observed_at_ms": observed,
        "exchange_time_ms": exchange,
        "research_symbol": "TAOUSDT",
        "executable_pair": "TAOUSD",
        "candles_15m": candles,
    }


def replay(decisions):
    return {
        "replay_id": "replay_fixture",
        "decisions": decisions,
    }


def decision(decision_id="d1", close_time=INTERVAL - 1, close=100):
    return {
        "decision_id": decision_id,
        "signal": "LONG_ELIGIBLE",
        "completed_candle_close_time_ms": close_time,
        "features": {"completed_close": Decimal(str(close))},
    }


class OutcomeLabelTests(unittest.TestCase):
    def setUp(self):
        self.path = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 100, 102, 98, 101),
            candle(2 * INTERVAL, 101, 102, 97, 99),
            candle(3 * INTERVAL, 99, 104, 98, 103),
            candle(4 * INTERVAL, 103, 105, 99, 104),
        ]
        self.snapshots = [
            snapshot(INTERVAL, INTERVAL, self.path[:2]),
            snapshot(5 * INTERVAL, 5 * INTERVAL, self.path),
        ]

    def test_labels_forward_return_and_excursions_without_fill_claim(self):
        result = build_outcome_artifact(
            self.snapshots,
            replay([decision()]),
            dataset_id="dataset_fixture",
            horizons_bars=(1, 4),
        )
        label = result["labels"][0]
        self.assertEqual(label["label_status"], "FULL")
        self.assertEqual(label["horizons"]["1"]["return_pct"], Decimal("1"))
        self.assertEqual(
            label["horizons"]["1"]["max_favorable_excursion_pct"], Decimal("2")
        )
        self.assertEqual(
            label["horizons"]["1"]["max_adverse_excursion_pct"], Decimal("-2")
        )
        self.assertEqual(label["horizons"]["4"]["return_pct"], Decimal("4"))
        self.assertEqual(
            label["horizons"]["4"]["max_favorable_excursion_pct"], Decimal("5")
        )
        self.assertEqual(
            label["horizons"]["4"]["max_adverse_excursion_pct"], Decimal("-3")
        )
        self.assertFalse(result["summary"]["performance_claim_available"])
        self.assertFalse(result["summary"]["fill_simulation_present"])

    def test_missing_path_is_unavailable_not_interpolated(self):
        later = snapshot(
            5 * INTERVAL,
            5 * INTERVAL,
            [self.path[0], self.path[1], self.path[3], self.path[4]],
        )
        result = build_outcome_artifact(
            [self.snapshots[0], later],
            replay([decision()]),
            dataset_id="dataset_fixture",
            horizons_bars=(1, 4),
        )
        label = result["labels"][0]
        self.assertEqual(label["label_status"], "PARTIAL")
        self.assertEqual(label["horizons"]["1"]["status"], "AVAILABLE")
        self.assertEqual(label["horizons"]["4"]["status"], "UNAVAILABLE")
        self.assertEqual(label["horizons"]["4"]["missing_candle_count"], 1)

    def test_incomplete_future_candle_is_not_used(self):
        only_early = [
            snapshot(INTERVAL, INTERVAL, self.path[:2]),
        ]
        result = build_outcome_artifact(
            only_early,
            replay([decision()]),
            dataset_id="dataset_fixture",
            horizons_bars=(1,),
        )
        self.assertEqual(
            result["labels"][0]["horizons"]["1"]["status"], "UNAVAILABLE"
        )

    def test_conflicting_historical_candle_fails_closed(self):
        changed = dict(self.path[1])
        changed["close"] = Decimal("100.5")
        with self.assertRaises(OutcomeLabelError):
            build_outcome_artifact(
                [
                    snapshot(3 * INTERVAL, 3 * INTERVAL, self.path[:2]),
                    snapshot(4 * INTERVAL, 4 * INTERVAL, [self.path[0], changed]),
                ],
                replay([decision()]),
                dataset_id="dataset_fixture",
                horizons_bars=(1,),
            )

    def test_feature_invalid_decision_is_preserved_as_unlabelable(self):
        result = build_outcome_artifact(
            self.snapshots,
            replay(
                [
                    {
                        "decision_id": "bad",
                        "signal": "DATA_STALE",
                        "completed_candle_close_time_ms": None,
                        "features": None,
                    }
                ]
            ),
            dataset_id="dataset_fixture",
            horizons_bars=(1,),
        )
        label = result["labels"][0]
        self.assertEqual(label["label_status"], "UNLABELABLE")
        self.assertEqual(label["horizons"], {})
        self.assertEqual(result["summary"]["unlabelable_count"], 1)

    def test_outcome_artifact_write_is_immutable(self):
        result = build_outcome_artifact(
            self.snapshots,
            replay([decision()]),
            dataset_id="dataset_fixture",
            horizons_bars=(1,),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outcomes.json"
            write_outcome_artifact(result, path)
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError):
                write_outcome_artifact(result, path)


if __name__ == "__main__":
    unittest.main()
