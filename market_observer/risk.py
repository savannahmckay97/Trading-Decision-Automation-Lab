"""Pure risk arithmetic. It cannot submit or authorize an order."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .util import decimal


def maximum_quantity(
    *,
    equity_usd: Any,
    risk_fraction: Any,
    entry_price: Any,
    invalidation_price: Any,
    round_trip_cost_bps: Any,
    max_notional_usd: Any | None = None,
) -> dict[str, Any]:
    equity = decimal(equity_usd)
    fraction = decimal(risk_fraction)
    entry = decimal(entry_price)
    invalidation = decimal(invalidation_price)
    cost_bps = decimal(round_trip_cost_bps)
    if equity <= 0 or entry <= 0 or invalidation <= 0:
        raise ValueError("equity and prices must be positive")
    if not Decimal("0") < fraction <= Decimal("0.01"):
        raise ValueError("risk_fraction must be positive and no greater than 1%")
    if cost_bps < 0 or entry == invalidation:
        raise ValueError("cost must be non-negative and stop must differ from entry")
    risk_budget = equity * fraction
    estimated_unit_cost = entry * cost_bps / Decimal("10000")
    unit_loss = abs(entry - invalidation) + estimated_unit_cost
    quantity = risk_budget / unit_loss
    if max_notional_usd is not None:
        cap = decimal(max_notional_usd)
        if cap <= 0:
            raise ValueError("max_notional_usd must be positive")
        quantity = min(quantity, cap / entry)
    return {
        "risk_budget_usd": risk_budget,
        "estimated_unit_cost_usd": estimated_unit_cost,
        "unit_loss_at_invalidation_usd": unit_loss,
        "maximum_quantity": quantity,
        "gap_loss_guaranteed": False,
    }
