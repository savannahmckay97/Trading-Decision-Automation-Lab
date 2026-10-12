from __future__ import annotations

import unittest
from decimal import Decimal

from market_observer.governor import (
    GovernorError,
    GovernorConfig,
    PaperAccount,
    assess_trade,
    build_governed_paper_artifact,
)
from market_observer.paper import PaperExecutionConfig
from test_paper import INTERVAL, candle, snapshot

DAY_BARS = 96


def decision(decision_id="d1", *, bar=0, signal="LONG_ELIGIBLE", invalidation="97.5", atr="2",
             spread="10", costs="30", risk_status="AVAILABLE"):
    return {
        "decision_id": decision_id,
        "signal": signal,
        "completed_candle_close_time_ms": bar * INTERVAL + INTERVAL - 1,
        "features": {"completed_close": Decimal("100"), "spread_bps": Decimal(spread)},
        "risk": {
            "status": risk_status,
            "reference_price": {"value": Decimal("100")},
            "invalidation": {"level": Decimal(invalidation), "derivation": {"breakout_atr": Decimal(atr)}},
            "costs": {"modelled_round_trip_bps": None if costs is None else Decimal(costs)},
        },
    }


def flat(bar):
    return candle(bar * INTERVAL, 100, 101, 99, 100)


def losing(bar):
    # Opens above invalidation, closes below it without touching the emergency stop.
    return candle(bar * INTERVAL, 101, 101, 97, 97)


def run(decisions, candles, *, hold=1, config=None):
    last = max(c["close_time_ms"] for c in candles) + 1
    return build_governed_paper_artifact(
        [snapshot(last, last, candles)],
        {"replay_id": "replay_fixture", "decisions": decisions},
        dataset_id="dataset_fixture",
        paper_config=PaperExecutionConfig(fee_bps_per_side=Decimal("5"),
                                          slippage_bps_per_side=Decimal("5"), max_hold_bars=hold),
        config=config or GovernorConfig(execution_model="MARKET_BASELINE"),
    )


def account(**kwargs):
    state = PaperAccount(equity_usd=Decimal("1000"))
    state.roll_to("1970-01-01")
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


class AssessTradeTests(unittest.TestCase):
    def test_sizes_on_emergency_stop_plus_costs(self):
        verdict = assess_trade(decision(), account(), GovernorConfig())
        self.assertEqual(verdict["status"], "APPROVED_FOR_PAPER")
        self.assertEqual(verdict["exits"]["emergency_stop"]["level"], Decimal("96.5"))
        self.assertEqual(verdict["risk_budget_usd"], Decimal("5.000"))
        # $5 / (3.5 distance + 0.30 round-trip cost per unit)
        self.assertEqual(verdict["quantity"], Decimal("5") / Decimal("3.8"))
        self.assertFalse(verdict["notional_cap_binding"])
        self.assertEqual(verdict["execution_permission"], "NONE")

    def test_no_leverage_cap_binds_on_tight_stops(self):
        verdict = assess_trade(decision(invalidation="99.9", atr="0.1"), account(), GovernorConfig())
        self.assertEqual(verdict["quantity"], Decimal("10"))
        self.assertTrue(verdict["notional_cap_binding"])

    def test_every_failed_check_is_reported(self):
        config = GovernorConfig(kill_switch=True)
        state = account(open_position={"decision_id": "earlier"}, consecutive_losses=3,
                        daily_realized_pnl_usd=Decimal("-20"))
        verdict = assess_trade(decision(costs=None), state, config)
        self.assertEqual(verdict["status"], "BLOCKED")
        self.assertEqual(verdict["reasons"], [
            "KILL_SWITCH_ENGAGED", "POSITION_LIMIT_REACHED", "DAILY_LOSS_LIMIT_REACHED",
            "CONSECUTIVE_LOSS_LIMIT_REACHED", "COSTS_INCOMPLETE",
        ])
        self.assertNotIn("quantity", verdict)

    def test_daily_loss_limit_is_two_percent_of_start_of_day(self):
        self.assertEqual(assess_trade(decision(), account(daily_realized_pnl_usd=Decimal("-19.99")),
                                      GovernorConfig())["status"], "APPROVED_FOR_PAPER")
        self.assertIn("DAILY_LOSS_LIMIT_REACHED",
                      assess_trade(decision(), account(daily_realized_pnl_usd=Decimal("-20")),
                                   GovernorConfig())["reasons"])

    def test_unavailable_risk_contract_fails_closed(self):
        verdict = assess_trade(decision(risk_status="WITHHELD_BLOCKED"), account(), GovernorConfig())
        self.assertIn("RISK_CONTRACT_UNAVAILABLE", verdict["reasons"])

    def test_non_proposals_are_not_assessed(self):
        verdict = assess_trade(decision(signal="WATCH_LONG"), account(), GovernorConfig())
        self.assertEqual(verdict["status"], "NOT_A_PROPOSAL")

    def test_configuration_rejects_unsafe_values(self):
        for kwargs in ({"risk_fraction": "0.02"}, {"max_notional_fraction": "2"},
                       {"max_open_positions": 2}, {"starting_equity_usd": "0"}):
            with self.assertRaises(ValueError):
                GovernorConfig(**kwargs)


class GovernedPaperTests(unittest.TestCase):
    def test_close_below_invalidation_exits_at_close(self):
        result = run([decision()], [flat(0), losing(1)])
        trade = result["trades"][0]
        self.assertEqual(trade["exit"]["reason"], "SETUP_INVALIDATED_ON_CLOSE")
        self.assertEqual(trade["exit"]["raw_price"], Decimal("97"))
        self.assertLess(result["summary"]["ending_equity_usd"], Decimal("1000"))

    def test_emergency_stop_gap_fills_at_worse_open(self):
        result = run([decision()], [flat(0), flat(1), candle(2 * INTERVAL, 95, 96, 94, 95)], hold=2)
        trade = result["trades"][0]
        self.assertEqual(trade["exit"]["reason"], "EMERGENCY_STOP")
        self.assertEqual(trade["exit"]["raw_price"], Decimal("95"))
        self.assertLess(trade["realized_r"], Decimal("-1"))

    def test_three_losses_stop_trading_until_next_utc_day(self):
        bars = [flat(0), losing(1), flat(2), losing(3), flat(4), losing(5), flat(6), flat(7),
                flat(DAY_BARS + 1), flat(DAY_BARS + 2)]
        decisions = [decision(f"d{bar}", bar=bar) for bar in (0, 2, 4, 6)]
        decisions.append(decision("next_day", bar=DAY_BARS + 1))
        result = run(decisions, bars)
        statuses = [(row["decision_id"], row["status"]) for row in result["journal"]]
        self.assertEqual(statuses, [
            ("d0", "APPROVED_FOR_PAPER"), ("d2", "APPROVED_FOR_PAPER"), ("d4", "APPROVED_FOR_PAPER"),
            ("d6", "BLOCKED"), ("next_day", "APPROVED_FOR_PAPER"),
        ])
        self.assertEqual(result["journal"][3]["reasons"], ["CONSECUTIVE_LOSS_LIMIT_REACHED"])
        self.assertEqual(result["summary"]["block_reason_counts"], {"CONSECUTIVE_LOSS_LIMIT_REACHED": 1})

    def test_open_position_blocks_overlapping_proposal(self):
        result = run([decision("a", bar=0), decision("b", bar=1)], [flat(0), flat(1), flat(2), flat(3)], hold=3)
        self.assertEqual(result["journal"][1]["reasons"], ["POSITION_LIMIT_REACHED"])

    def test_censored_path_ends_the_simulation(self):
        result = run([decision("a", bar=0), decision("b", bar=5)], [flat(0)], hold=2)
        self.assertEqual(result["summary"]["censored_at_decision_id"], "a")
        self.assertEqual(result["journal"][1]["status"], "NOT_EVALUATED")
        self.assertEqual(result["trades"], [])

    def test_summary_never_claims_performance_or_permission(self):
        result = run([decision()], [flat(0), flat(1)])
        self.assertFalse(result["summary"]["performance_claim_available"])
        self.assertEqual(result["summary"]["execution_permission"], "NONE")
        self.assertTrue(all(row["execution_permission"] == "NONE" for row in result["journal"]))


def maker(**kwargs):
    return GovernorConfig(maker_fee_bps="2", **kwargs)


def bar(i, o, h, l, c):
    return candle(i * INTERVAL, o, h, l, c)


class MakerStaggeredTests(unittest.TestCase):
    # decision(): reference 100, emergency stop 96.5 (R = 3.5), trail 2 * ATR 2 = 4.
    def test_requires_a_maker_fee_and_never_assumes_zero(self):
        with self.assertRaises(GovernorError):
            run([decision()], [flat(0), flat(1)], config=GovernorConfig())

    def test_sizing_cost_is_maker_entry_plus_taker_exit(self):
        result = run([decision()], [flat(0), bar(1, 100, 100.5, 99.5, 100), flat(2)], config=maker())
        # 2 maker + 5 taker + 5 slippage + half of the 10 bps spread
        self.assertEqual(result["journal"][0]["round_trip_cost_bps"], Decimal("17"))

    def test_unfilled_entry_expires_without_touching_the_account(self):
        up = lambda i: bar(i, 101, 103, 100.5, 102)
        result = run([decision()], [flat(0), up(1), up(2), up(3)], hold=3, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_FILLED_TTL_EXPIRED")
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["summary"]["ending_equity_usd"], Decimal("1000"))

    def test_touch_is_not_a_fill(self):
        touch = lambda i: bar(i, 101, 102, 100, 101)  # low equals the limit exactly
        result = run([decision()], [flat(0), touch(1), touch(2), touch(3)], hold=3, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_FILLED_TTL_EXPIRED")

    def test_staggered_targets_fill_as_maker_then_trail_stops_the_rest(self):
        bars = [flat(0), bar(1, 100, 100.5, 99.5, 100), bar(2, 100, 104, 100, 103.5),
                bar(3, 103.5, 108.5, 103.5, 108), bar(4, 108, 108, 102, 103), flat(5)]
        trade = run([decision()], bars, hold=5, config=maker())["trades"][0]
        self.assertEqual([(l["reason"], l["liquidity"]) for l in trade["legs"]],
                         [("TAKE_PROFIT_1R", "MAKER"), ("TAKE_PROFIT_2R", "MAKER"), ("TRAILING_STOP", "TAKER")])
        self.assertEqual(trade["legs"][0]["raw_price"], Decimal("103.5"))
        self.assertEqual(trade["legs"][1]["raw_price"], Decimal("107"))
        # trail = highest completed high 108.5 - 4 = 104.5; the bar opens above it, so fills at the level
        self.assertEqual(trade["legs"][2]["raw_price"], Decimal("104.5"))
        self.assertGreater(trade["realized_r"], 0)
        self.assertEqual(trade["maker_leg_share"], Decimal(2) / 3)

    def test_stop_beats_target_inside_one_bar(self):
        bars = [flat(0), bar(1, 100, 100.5, 99.5, 100), bar(2, 100, 110, 96, 100), flat(3)]
        trade = run([decision()], bars, hold=3, config=maker())["trades"][0]
        self.assertEqual([l["reason"] for l in trade["legs"]], ["EMERGENCY_STOP"])
        self.assertLess(trade["realized_r"], 0)

    def test_targets_are_not_credited_on_the_entry_bar(self):
        bars = [flat(0), bar(1, 100, 105, 99.5, 104), bar(2, 104, 104, 103, 103.5)]
        trade = run([decision()], bars, hold=2, config=maker())["trades"][0]
        entry_bar = trade["entry"]["bar_close_time_ms"]
        self.assertFalse(any(l["reason"].startswith("TAKE_PROFIT") and l["bar_close_time_ms"] == entry_bar
                             for l in trade["legs"]))
        # The entry bar's high (105) reached +1R (103.5) but isn't credited; bar 2's high (104) is.
        self.assertEqual(trade["legs"][0]["reason"], "TAKE_PROFIT_1R")
        self.assertEqual(trade["legs"][0]["bar_close_time_ms"], bars[2]["close_time_ms"])

    def test_trail_only_ratchets_up(self):
        bars = [flat(0), bar(1, 100, 100.5, 99.5, 100), bar(2, 100, 107, 100, 106),
                bar(3, 106, 106, 103.5, 104), bar(4, 104, 104, 100.5, 101)]
        trade = run([decision()], bars, hold=4, config=maker())["trades"][0]
        # after bar 2 the level is 107 - 4 = 103; bar 3 (high 106 < 107) cannot lower it
        self.assertEqual(trade["legs"][-1]["reason"], "TRAILING_STOP")
        self.assertEqual(trade["legs"][-1]["raw_price"], Decimal("103"))

    def test_tranche_configuration_must_leave_a_trailing_remainder(self):
        with self.assertRaises(ValueError):
            maker(take_profit_tranches=((1, "0.6"), (2, "0.4")))



class ReviewRegressionTests(unittest.TestCase):
    """Reproductions from the independent review in issue #22 (G1-G7, settlement)."""

    def test_g1_a_filled_entry_is_not_cancelled_by_a_later_close(self):
        # limit 100, invalidation 97.5, emergency 96.5; the bar trades 100 -> 95 and closes 96
        result = run([decision()], [flat(0), bar(1, 100, 101, 95, 96)], hold=2, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "FILLED")
        trade = result["trades"][0]
        self.assertEqual(trade["exit"]["reason"], "EMERGENCY_STOP")
        self.assertLess(trade["net_pnl_usd"], 0)
        self.assertLess(result["summary"]["ending_equity_usd"], Decimal("1000"))

    def test_g1_close_rule_after_a_fill_exits_the_position(self):
        # fills through 100, stays above the 96.5 floor, closes below invalidation 97.5
        trade = run([decision()], [flat(0), bar(1, 100, 100.5, 97, 97.2)], hold=2, config=maker())["trades"][0]
        self.assertEqual(trade["exit"]["reason"], "SETUP_INVALIDATED_ON_CLOSE")
        self.assertEqual(trade["exit"]["raw_price"], Decimal("97.2"))

    def test_g2_entry_bar_stop_does_not_use_the_entry_bars_own_high(self):
        # entry 100, floor 96.5, trail 4; entry bar 100/110/99/108 must not stop at 106
        for high in ("110", "101"):
            bars = [flat(0), bar(1, 100, high, 99, 100.8 if high == "101" else 108), flat(2)]
            trade = run([decision()], bars, hold=2, config=maker())["trades"][0]
            entry_bar = trade["entry"]["bar_close_time_ms"]
            self.assertFalse(any(l["bar_close_time_ms"] == entry_bar for l in trade["legs"]), high)

    def test_g3_stale_records_and_duplicate_polls_do_not_abort_the_run(self):
        stale = {"decision_id": "stale", "signal": "DATA_STALE", "completed_candle_close_time_ms": None,
                 "observed_at_ms": 1, "snapshot_index": 0}
        first, repeat = decision("first"), decision("repeat")
        first.update(observed_at_ms=2, snapshot_index=1)
        repeat.update(observed_at_ms=3, snapshot_index=2)
        result = run([stale, first, repeat], [flat(0), flat(1)], config=GovernorConfig(execution_model="MARKET_BASELINE"))
        self.assertEqual([row["decision_id"] for row in result["journal"]], ["first"])
        self.assertEqual(result["summary"]["duplicate_polls_count"], 1)
        self.assertEqual(result["summary"]["non_proposals_count"], 1)

    def test_g3_out_of_order_observations_are_rejected(self):
        a, b = decision("a"), decision("b")
        a.update(observed_at_ms=5, snapshot_index=1)
        b.update(observed_at_ms=4, snapshot_index=2)
        with self.assertRaises(GovernorError):
            run([a, b], [flat(0), flat(1)])

    def test_g4_no_leverage_and_risk_hold_at_the_actual_fill(self):
        # reference-sized at 100 with the cap binding (quantity 10); the next bar opens at 200
        result = run([decision(invalidation="99.9", atr="0.1")], [flat(0), bar(1, 200, 201, 199.5, 200)])
        trade = result["trades"][0]
        entry = trade["entry"]["effective_price"]
        self.assertLessEqual(trade["quantity"] * entry * (1 + Decimal("5") / 10000), Decimal("1000"))
        self.assertLess(trade["quantity"], trade["reference_quantity"])
        self.assertIn("RESIZED_TO_RISK_AT_FILL", trade["resize_reasons"])

    def test_g4_cash_cap_binds_when_risk_does_not(self):
        verdict = assess_trade(decision(invalidation="99.9", atr="0.1"), account(), GovernorConfig())
        from market_observer.governor import _committed_quantity
        quantity, reasons = _committed_quantity(verdict, Decimal("101"), Decimal("5"), Decimal("99.85"),
                                                Decimal("1000"), GovernorConfig())
        self.assertIn("RESIZED_TO_CASH_AT_FILL", reasons)
        self.assertLessEqual(quantity * Decimal("101") * (1 + Decimal("5") / 10000), Decimal("1000"))

    def test_g5_targets_use_the_committed_maker_entry(self):
        # reference 100, first bar opens at 99 so the limit is 99; stop 96.5, so 1R = 2.5 and +1R = 101.5
        bars = [flat(0), bar(1, 99, 99.5, 98.5, 99), bar(2, 99, 102, 99, 101.8), flat(3)]
        trade = run([decision()], bars, hold=3, config=maker())["trades"][0]
        self.assertEqual(trade["price_r_unit"], Decimal("2.5"))
        self.assertEqual(trade["take_profit_levels"][0], Decimal("101.5"))
        self.assertEqual(trade["legs"][0]["raw_price"], Decimal("101.5"))

    def test_g6_missing_bars_after_an_exit_do_not_censor_it(self):
        # entry and emergency stop both in bar 1; bars 2-3 absent with a 3-bar hold
        result = run([decision()], [flat(0), bar(1, 100, 100, 95, 96)], hold=3)
        self.assertEqual(result["journal"][0]["paper_outcome"], "FILLED")
        self.assertIsNone(result["summary"]["censored_at_decision_id"])
        self.assertLess(result["summary"]["ending_equity_usd"], Decimal("1000"))

    def test_g6_missing_bars_after_an_expired_order_do_not_censor_it(self):
        up = lambda i: bar(i, 101, 103, 100.5, 102)
        result = run([decision()], [flat(0), up(1), up(2)], hold=4, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_FILLED_TTL_EXPIRED")
        self.assertIsNone(result["summary"]["censored_at_decision_id"])

    def test_g6_missing_bar_while_exposed_still_censors(self):
        result = run([decision()], [flat(0), flat(1)], hold=3)
        self.assertEqual(result["summary"]["censored_at_decision_id"], "d1")

    def test_g7_malformed_geometry_costs_and_account_block_explicitly(self):
        cases = [
            (decision(atr="-1"), account(), None, "INVALID_STOP_GEOMETRY"),
            (decision(atr="0"), account(), None, "INVALID_STOP_GEOMETRY"),
            (decision(invalidation="101"), account(), None, "INVALID_STOP_GEOMETRY"),
            (decision(atr="NaN"), account(), None, "RISK_CONTRACT_MALFORMED"),
            (decision(costs="-1"), account(), None, "COSTS_INVALID"),
            (decision(), account(), "Infinity", "COSTS_INVALID"),
            (decision(), account(equity_usd=Decimal("0")), None, "ACCOUNT_STATE_INVALID"),
        ]
        for item, state, cost, reason in cases:
            verdict = assess_trade(item, state, GovernorConfig(), cost)
            self.assertEqual(verdict["status"], "BLOCKED", reason)
            self.assertIn(reason, verdict["reasons"])

    def test_g7_missing_spread_blocks_in_a_governed_run(self):
        item = decision()
        del item["features"]["spread_bps"]
        result = run([item], [flat(0), flat(1)])
        self.assertEqual(result["journal"][0]["reasons"], ["COSTS_INCOMPLETE"])

    def test_legs_are_booked_on_their_own_utc_day(self):
        a = DAY_BARS - 4
        bars = [flat(a), bar(a + 1, 100, 100.5, 99.5, 100), bar(a + 2, 100, 104, 100, 103.6),
                bar(a + 3, 103.6, 103.8, 103, 103.5), bar(a + 4, 103.5, 103.6, 97.3, 97.4),
                flat(a + 5), flat(a + 6), flat(a + 7)]
        later = DAY_BARS + 2
        result = run([decision("overnight", bar=a), decision("later", bar=later)], bars, hold=6, config=maker())
        trade = result["trades"][0]
        before_midnight = [e for e in trade["events"] if e["time_ms"] < DAY_BARS * INTERVAL]
        after_midnight = [e for e in trade["events"] if e["time_ms"] >= DAY_BARS * INTERVAL]
        self.assertEqual([e["kind"] for e in before_midnight], ["ENTRY_FEE", "TAKE_PROFIT_1R"])
        self.assertEqual([e["kind"] for e in after_midnight], ["TRAILING_STOP"])
        seen = result["journal"][1]["account_before"]
        self.assertEqual(seen["start_of_day_equity_usd"],
                         Decimal("1000") + sum(e["pnl_usd"] for e in before_midnight))
        self.assertEqual(seen["daily_realized_pnl_usd"], after_midnight[0]["pnl_usd"])
        # The "later" trade fills and is then censored by missing bars: its known entry fee is booked too.
        self.assertEqual(result["trades"][1]["status"], "CENSORED")
        booked = sum(e["pnl_usd"] for t in result["trades"] for e in t["events"])
        self.assertEqual(result["summary"]["ending_equity_usd"], Decimal("1000") + booked)

if __name__ == "__main__":
    unittest.main()
