from __future__ import annotations

import unittest
from decimal import Decimal

from market_observer.governor import (
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
        config=config or GovernorConfig(),
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


if __name__ == "__main__":
    unittest.main()
