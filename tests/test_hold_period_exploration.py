from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "hold_period_exploration"))
import explore  # noqa: E402

T0 = 1_700_000_000_000 - (1_700_000_000_000 % 900_000)


def prices(rows):
    """rows: (open, high, low, close) per contiguous 15m candle."""
    bars = [{"open_time_ms": T0 + k * 900_000, "open": o, "high": h, "low": low, "close": c}
            for k, (o, h, low, c) in enumerate(rows)]
    return explore.Prices(bars)


class HoldPeriodExplorationTest(unittest.TestCase):
    def test_entry_is_the_candle_after_next(self):
        p = prices([(100, 101, 99, 100)] * 5)
        self.assertEqual(p.entry_index(0), 2)

    def test_gaps_are_rejected(self):
        bars = [{"open_time_ms": T0, "open": 1, "high": 1, "low": 1, "close": 1},
                {"open_time_ms": T0 + 1_800_000, "open": 1, "high": 1, "low": 1, "close": 1}]
        with self.assertRaises(ValueError):
            explore.Prices(bars)

    def test_close_stop_exits_at_breaching_close_not_on_wick(self):
        p = prices([(100, 101, 94, 99), (99, 99, 96, 97), (97, 98, 90, 91)])
        self.assertEqual(explore.first_stop_exit(p, 0, "b_close_stop", 95, 1, 10), (2, 91))

    def test_wick_stop_fills_at_level_or_worse_open(self):
        p = prices([(100, 101, 96, 99), (99, 99, 94, 97)])
        self.assertEqual(explore.first_stop_exit(p, 0, "c_wick_stop", 95, 1, 10), (1, 95))
        gap = prices([(100, 101, 96, 99), (93, 94, 92, 93)])
        self.assertEqual(explore.first_stop_exit(gap, 0, "c_wick_stop", 95, 1, 10), (1, 93))
        self.assertEqual(explore.first_stop_exit(gap, 0, "d1_wick_inv_minus_1atr", 95, 1, 10), (1, 93))
        self.assertIsNone(explore.first_stop_exit(gap, 0, "d2_wick_inv_minus_2atr", 95, 2, 10))

    def test_trailing_stop_ratchets_after_completed_candles_only(self):
        # inv 90, atr 1: initial stop 88; after candle 0 (high 120) stop = 110.
        p = prices([(100, 120, 89, 115), (115, 116, 111, 112), (112, 113, 105, 106)])
        self.assertEqual(explore.first_stop_exit(p, 0, "e_trail_10atr", 90, 1, 10), (2, 110))
        # Candle 0's own high cannot stop candle 0 out.
        self.assertIsNone(explore.first_stop_exit(prices([(100, 120, 95, 96)]), 0, "e_trail_10atr", 90, 1, 10))

    def test_simulate_time_exit_and_censoring(self):
        p = prices([(100, 101, 99, 100)] * 3 + [(100, 101, 99, 110)])
        out = explore.simulate(p, 0, 50, 1)
        x, gross, net = out[("1h", "a_time")]
        self.assertEqual((x, round(gross, 9)), (3, 0.1))
        self.assertAlmostEqual(net["maker_in_taker_out"], 110 * (1 - 0.0041675) / (100 * (1 + 0.0026675)) - 1)
        self.assertIsNone(out[("4h", "a_time")])

    def test_one_at_a_time_skips_overlapping_signals(self):
        import datetime as dt
        day = dt.date(2025, 1, 1)
        net = {k: 0.01 for k in explore.COSTS}
        trades = [{"day": day, "entry_index": 2, "exit_index": 10, "gross": 0.02, "net": net, "r": 1.0},
                  {"day": day, "entry_index": 10, "exit_index": 12, "gross": 0.02, "net": net, "r": 1.0},
                  {"day": day, "entry_index": 11, "exit_index": 20, "gross": 0.02, "net": net, "r": 1.0}]
        out = explore.one_at_a_time(trades, 100)
        self.assertEqual(out["n"], 2)
        self.assertAlmostEqual(out["compounded_return_pct_maker_in_taker_out"], 100 * (1.01 ** 2 - 1))
        self.assertAlmostEqual(out["time_in_market_fraction"], 19 / 100)
        self.assertIsNone(out["ci95_mean_net_pct_maker_in_taker_out"])  # one day: no interval

    def test_round_trip_costs(self):
        rt = {k: round(10_000 * (a + b), 6) for k, (a, b) in explore.COSTS.items()}
        self.assertEqual(rt, {"maker_in_taker_out": 68.35, "taker_in_taker_out": 83.35})


if __name__ == "__main__":
    unittest.main()
