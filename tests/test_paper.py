from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market_observer.paper import (
    PaperExecutionConfig,
    build_paper_fill_artifact,
    write_paper_fill_artifact,
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


def eligible_decision(
    decision_id="d1",
    *,
    anchor_close=INTERVAL - 1,
    spread_bps="10",
    resistance="99",
    atr="10",
):
    return {
        "decision_id": decision_id,
        "signal": "LONG_ELIGIBLE",
        "completed_candle_close_time_ms": anchor_close,
        "features": {
            "completed_close": Decimal("100"),
            "spread_bps": Decimal(str(spread_bps)),
        },
        "setup": {
            "breakout_resistance": Decimal(str(resistance)),
            "breakout_atr": Decimal(str(atr)),
        },
    }


def replay(decisions):
    return {
        "replay_id": "replay_fixture",
        "baseline": {
            "configuration": {
                "failure_buffer_atr": "0.15",
            }
        },
        "decisions": decisions,
    }


class PaperFillTests(unittest.TestCase):
    def config(self, hold=2):
        return PaperExecutionConfig(
            fee_bps_per_side=Decimal("5"),
            slippage_bps_per_side=Decimal("5"),
            max_hold_bars=hold,
        )

    def test_costs_reduce_horizon_exit_return(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 101, 103, 100, 102),
            candle(2 * INTERVAL, 102, 104, 101, 103),
        ]
        result = build_paper_fill_artifact(
            [snapshot(3 * INTERVAL, 3 * INTERVAL, candles)],
            replay([eligible_decision()]),
            dataset_id="dataset_fixture",
            config=self.config(),
        )
        fill = result["fills"][0]
        self.assertEqual(fill["status"], "FILLED")
        self.assertEqual(fill["exit"]["reason"], "MAX_HOLD")
        self.assertEqual(fill["entry"]["raw_price"], Decimal("101"))
        self.assertEqual(fill["exit"]["raw_price"], Decimal("103"))
        self.assertLess(
            fill["net_return_pct_on_effective_entry"],
            fill["gross_return_pct"],
        )
        self.assertFalse(fill["executable_venue_validated"])
        self.assertFalse(result["summary"]["performance_claim_available"])
        self.assertFalse(result["summary"]["risk_governor_present"])

    def test_stop_invalidation_uses_strategy_failure_level(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 101, 102, 98, 99),
            candle(2 * INTERVAL, 99, 101, 98, 100),
        ]
        result = build_paper_fill_artifact(
            [snapshot(3 * INTERVAL, 3 * INTERVAL, candles)],
            replay([eligible_decision(resistance="100", atr="10")]),
            dataset_id="dataset_fixture",
            config=self.config(),
        )
        fill = result["fills"][0]
        self.assertEqual(fill["exit"]["reason"], "INVALIDATION")
        self.assertEqual(fill["invalidation_price"], Decimal("98.50"))
        self.assertEqual(fill["exit"]["raw_price"], Decimal("98.50"))
        self.assertEqual(fill["holding_bars"], 1)

    def test_gap_through_stop_uses_worse_bar_open(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 101, 103, 100, 102),
            candle(2 * INTERVAL, 97, 99, 96, 98),
        ]
        result = build_paper_fill_artifact(
            [snapshot(3 * INTERVAL, 3 * INTERVAL, candles)],
            replay([eligible_decision(resistance="100", atr="10")]),
            dataset_id="dataset_fixture",
            config=self.config(),
        )
        fill = result["fills"][0]
        self.assertEqual(fill["exit"]["reason"], "INVALIDATION")
        self.assertEqual(fill["exit"]["raw_price"], Decimal("97"))
        self.assertLess(fill["exit"]["raw_price"], fill["invalidation_price"])

    def test_invalidated_before_intended_entry_is_not_filled(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 98, 100, 97, 99),
        ]
        result = build_paper_fill_artifact(
            [snapshot(2 * INTERVAL, 2 * INTERVAL, candles)],
            replay([eligible_decision(resistance="100", atr="10")]),
            dataset_id="dataset_fixture",
            config=self.config(hold=1),
        )
        fill = result["fills"][0]
        self.assertEqual(fill["status"], "INVALIDATED_BEFORE_ENTRY")
        self.assertEqual(result["summary"]["filled_count"], 0)

    def test_missing_future_path_fails_closed(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 101, 103, 100, 102),
        ]
        result = build_paper_fill_artifact(
            [snapshot(2 * INTERVAL, 2 * INTERVAL, candles)],
            replay([eligible_decision()]),
            dataset_id="dataset_fixture",
            config=self.config(hold=2),
        )
        fill = result["fills"][0]
        self.assertEqual(fill["status"], "UNAVAILABLE")
        self.assertEqual(fill["reason"], "incomplete_future_path")
        self.assertEqual(fill["missing_candle_count"], 1)

    def test_noneligible_signals_do_not_create_paper_fills(self):
        result = build_paper_fill_artifact(
            [
                snapshot(
                    2 * INTERVAL,
                    2 * INTERVAL,
                    [
                        candle(0, 99, 101, 98, 100),
                        candle(INTERVAL, 101, 103, 100, 102),
                    ],
                )
            ],
            replay(
                [
                    {
                        **eligible_decision(),
                        "signal": "WATCH_LONG",
                    }
                ]
            ),
            dataset_id="dataset_fixture",
            config=self.config(hold=1),
        )
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["summary"]["eligible_signal_count"], 0)

    def test_negative_cost_assumptions_are_rejected(self):
        with self.assertRaises(ValueError):
            PaperExecutionConfig(
                fee_bps_per_side=Decimal("-1"),
                slippage_bps_per_side=Decimal("0"),
                max_hold_bars=1,
            )

    def test_paper_fill_artifact_write_is_immutable(self):
        candles = [
            candle(0, 99, 101, 98, 100),
            candle(INTERVAL, 101, 103, 100, 102),
        ]
        result = build_paper_fill_artifact(
            [snapshot(2 * INTERVAL, 2 * INTERVAL, candles)],
            replay([eligible_decision()]),
            dataset_id="dataset_fixture",
            config=self.config(hold=1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            write_paper_fill_artifact(result, path)
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError):
                write_paper_fill_artifact(result, path)


if __name__ == "__main__":
    unittest.main()
