from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from decimal import Decimal

from market_observer.config import ObserverConfig
from market_observer.evaluator import evaluate_snapshot
from market_observer.normalization import build_snapshot
from market_observer.reward_reference import assess_reward_reference
from market_observer.util import canonical_json
from test_observer import INTERVAL, events_for


START = 1_000_000_000_000


def candle_snapshot(highs, *, open_candle_high=None):
    """Completed candles with the given highs (low 90, close 95) plus one open candle."""
    rows = []
    for index, high in enumerate(highs):
        open_time = START + index * INTERVAL
        rows.append({
            "open_time_ms": open_time,
            "close_time_ms": open_time + INTERVAL - 1,
            "open": Decimal("95"), "high": Decimal(str(high)), "low": Decimal("90"),
            "close": Decimal("95"), "volume": Decimal("10"),
        })
    open_time = START + len(highs) * INTERVAL
    rows.append({
        "open_time_ms": open_time, "close_time_ms": open_time + INTERVAL - 1,
        "open": Decimal("95"), "high": Decimal(str(open_candle_high or 96)), "low": Decimal("90"),
        "close": Decimal("95"), "volume": Decimal("1"),
    })
    return {"exchange_time_ms": open_time + 1_000, "candles_15m": rows}


def reward(snapshot, *, reference="95", breakout_index=None, costs=None, config=None):
    config = config or ObserverConfig()
    completed = [row for row in snapshot["candles_15m"] if row["close_time_ms"] < snapshot["exchange_time_ms"]]
    breakout = completed[breakout_index if breakout_index is not None else -1]["close_time_ms"]
    return assess_reward_reference(
        snapshot,
        reference=Decimal(reference),
        reference_close_time_ms=completed[-1]["close_time_ms"],
        stop_distance=Decimal("2"),
        atr=Decimal("1"),
        breakout_close_time_ms=breakout,
        modelled_round_trip_bps=costs,
        config=config,
    )


def fixture(stage, **kwargs):
    return build_snapshot(*events_for(stage, **kwargs), "TAOUSDT", "TAOUSD")


class SelectionTests(unittest.TestCase):
    def test_nearest_confirmed_level_above_reference_is_selected(self):
        highs = [96] * 30
        highs[5], highs[12], highs[20] = 110, 99, 104
        result = reward(candle_snapshot(highs))
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["level"], Decimal("99"))
        self.assertEqual(result["source"]["bars_before_reference"], 29 - 12)
        self.assertEqual(result["source"]["confirmed_by_close_time_ms"], START + 14 * INTERVAL + INTERVAL - 1)
        self.assertEqual(result["gross_reward"]["distance"], Decimal("4"))
        self.assertEqual(result["gross_opportunity_to_risk"], Decimal("2"))
        self.assertEqual(result["ratio_basis"], "GROSS_PRICE_DISTANCES_NO_COSTS")

    def test_equal_pivots_report_most_recent_and_count(self):
        highs = [96] * 30
        highs[6] = highs[18] = 100
        result = reward(candle_snapshot(highs))
        self.assertEqual(result["source"]["pivot_count_at_level"], 2)
        self.assertEqual(result["source"]["bars_before_reference"], 29 - 18)

    def test_flat_top_yields_one_pivot_at_earliest_bar(self):
        highs = [96] * 30
        highs[10] = highs[11] = 100
        result = reward(candle_snapshot(highs))
        self.assertEqual(result["confirmed_pivots_in_window"], 1)
        self.assertEqual(result["source"]["pivot_open_time_ms"], START + 10 * INTERVAL)

    def test_setup_internal_pivot_is_used_and_tagged(self):
        highs = [96] * 30
        highs[10], highs[25] = 110, 100
        result = reward(candle_snapshot(highs), breakout_index=24)
        self.assertEqual(result["level"], Decimal("100"))
        self.assertTrue(result["source"]["formed_during_setup"])

    def test_costs_only_adjust_ratio_when_modelled_total_is_known(self):
        highs = [96] * 30
        highs[10] = 100
        gross = reward(candle_snapshot(highs))
        self.assertIsNone(gross["cost_adjusted_opportunity_to_risk"])
        self.assertEqual(gross["cost_adjusted_basis"], "UNAVAILABLE_COSTS_INCOMPLETE")
        adjusted = reward(candle_snapshot(highs), costs=Decimal("20"))
        reward_bps = Decimal("5") / Decimal("95") * 10000
        risk_bps = Decimal("2") / Decimal("95") * 10000
        self.assertEqual(
            adjusted["cost_adjusted_opportunity_to_risk"],
            (reward_bps - 20) / (risk_bps + 20),
        )
        self.assertEqual(adjusted["cost_adjusted_basis"], "PARTIAL_EXCLUDES_FUNDING_AND_GAP")


class MissingAndInvalidTargetTests(unittest.TestCase):
    def test_no_level_above_reference_is_target_unavailable(self):
        highs = [96] * 30
        highs[10] = Decimal("96.5")  # a confirmed pivot, but below the reference
        result = reward(candle_snapshot(highs), reference="97")
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")
        self.assertEqual(result["reason"], "NO_CONFIRMED_LEVEL_ABOVE_REFERENCE_IN_WINDOW")
        self.assertEqual(result["search_window"]["bars"], 30)
        self.assertNotIn("level", result)
        self.assertNotIn("gross_opportunity_to_risk", result)

    def test_level_equal_to_reference_does_not_qualify(self):
        highs = [96] * 30
        highs[10] = 100
        result = reward(candle_snapshot(highs), reference="100")
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")

    def test_no_atr_r_multiple_or_measured_move_substitute(self):
        result = reward(candle_snapshot([96] * 30), reference="97")
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")
        self.assertEqual(result["method"], "CONFIRMED_SWING_HIGH")
        self.assertFalse({"distance", "gross_reward", "level"} & set(result))

    def test_gap_shortens_search_and_is_disclosed(self):
        highs = [96] * 30
        highs[3] = 100
        snapshot = candle_snapshot(highs)
        del snapshot["candles_15m"][8]
        result = reward(snapshot)
        self.assertTrue(result["search_window"]["truncated_by_gap"])
        self.assertEqual(result["search_window"]["bars"], 30 - 9)
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")  # the level before the gap is not used

    def test_duplicate_candle_fails_closed(self):
        snapshot = candle_snapshot([96] * 30)
        snapshot["candles_15m"].insert(5, copy.deepcopy(snapshot["candles_15m"][5]))
        result = reward(snapshot)
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")
        self.assertTrue(result["reason"].startswith("INVALID_SEARCH_WINDOW"))

    def test_invalid_ohlcv_fails_closed(self):
        highs = [96] * 30
        highs[10] = 100
        snapshot = candle_snapshot(highs)
        snapshot["candles_15m"][10]["low"] = Decimal("101")
        self.assertEqual(reward(snapshot)["status"], "TARGET_UNAVAILABLE")

    def test_window_must_end_at_reference_candle(self):
        snapshot = candle_snapshot([96] * 30)
        config = ObserverConfig()
        result = assess_reward_reference(
            snapshot, reference=Decimal("95"), reference_close_time_ms=START, stop_distance=Decimal("2"),
            atr=Decimal("1"), breakout_close_time_ms=START, modelled_round_trip_bps=None, config=config,
        )
        self.assertEqual(result["reason"], "SEARCH_WINDOW_NOT_ANCHORED_TO_REFERENCE")


class LookAheadTests(unittest.TestCase):
    def test_pivot_needing_open_candle_for_confirmation_is_not_used(self):
        highs = [96] * 30
        highs[28] = 100  # confirmation needs bars 29 and 30; bar 30 is still open
        result = reward(candle_snapshot(highs, open_candle_high=95))
        self.assertEqual(result["status"], "TARGET_UNAVAILABLE")
        self.assertEqual(result["unconfirmed_overhead_high"]["level"], Decimal("100"))

    def test_open_and_future_candles_cannot_change_result(self):
        highs = [96] * 30
        highs[10] = 100
        baseline = reward(candle_snapshot(highs))
        altered = candle_snapshot(highs, open_candle_high=97)
        future_open = altered["candles_15m"][-1]["open_time_ms"] + INTERVAL
        altered["candles_15m"].append({
            "open_time_ms": future_open, "close_time_ms": future_open + INTERVAL - 1,
            "open": Decimal("95"), "high": Decimal("98"), "low": Decimal("90"),
            "close": Decimal("95"), "volume": Decimal("1"),
        })
        self.assertEqual(canonical_json(reward(altered)), canonical_json(baseline))

    def test_prefix_parity_with_truncated_history(self):
        highs = [96] * 40
        highs[10], highs[33] = 100, 99
        full = candle_snapshot(highs)
        at_t = candle_snapshot(highs[:30])
        # Same candles, but exchange time at bar 30: later bars are not yet complete.
        clipped = copy.deepcopy(full)
        clipped["exchange_time_ms"] = at_t["exchange_time_ms"]
        self.assertEqual(canonical_json(reward(clipped)), canonical_json(reward(at_t)))
        self.assertEqual(reward(clipped)["level"], Decimal("100"))

    def test_recent_unconfirmed_high_is_disclosed_not_used(self):
        highs = [96] * 30
        highs[10], highs[29] = 104, 99
        result = reward(candle_snapshot(highs))
        self.assertEqual(result["level"], Decimal("104"))
        self.assertEqual(result["unconfirmed_overhead_high"]["level"], Decimal("99"))
        self.assertTrue(result["target_beyond_unconfirmed_high"])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = ObserverConfig()

    def test_fixture_breakout_has_no_overhead_and_says_so(self):
        risk = evaluate_snapshot(fixture("breakout"), self.config, None).risk
        target = risk["reward_reference"]
        self.assertEqual(target["status"], "TARGET_UNAVAILABLE")
        self.assertEqual(target["unconfirmed_overhead_high"]["level"], Decimal("104"))

    def test_blocked_cycle_withholds_reward(self):
        first = evaluate_snapshot(fixture("breakout"), self.config, None)
        blocked = evaluate_snapshot(fixture("retest", spread=True, received_offset=1), self.config, first.state)
        self.assertEqual(blocked.risk["status"], "WITHHELD_BLOCKED")
        self.assertNotIn("reward_reference", blocked.risk)

    def test_reward_never_changes_eligibility(self):
        plain = fixture("breakout")
        peaked = copy.deepcopy(plain)
        # Bar 2 lies outside every feature window (last 97 bars) but inside the
        # reward search, and has the two left-hand bars a pivot needs.
        for row in peaked["candles_15m"][2:3]:
            row["high"] = Decimal("120")
        a = evaluate_snapshot(plain, self.config, None)
        b = evaluate_snapshot(peaked, self.config, None)
        self.assertEqual((a.signal, a.state, a.gates, a.features), (b.signal, b.state, b.gates, b.features))
        self.assertEqual(b.risk["reward_reference"]["status"], "AVAILABLE")
        self.assertEqual(b.risk["reward_reference"]["level"], Decimal("120"))
        self.assertNotEqual(a.risk["reward_reference"]["status"], "AVAILABLE")

    def test_configuration_rejects_unfittable_search(self):
        for kwargs in ({"reward_pivot_confirmation_bars": 0}, {"reward_search_bars": 4},
                       {"reward_search_bars": 150}):
            with self.assertRaises(ValueError):
                replace(self.config, **kwargs).validate()


if __name__ == "__main__":
    unittest.main()
