"""Pure causal feature calculations over completed data only."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .config import ObserverConfig
from .util import decimal


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
TEN_THOUSAND = Decimal("10000")


class FeatureError(ValueError):
    pass


def calculate_features(snapshot: dict[str, Any], config: ObserverConfig) -> dict[str, Any]:
    candles = _completed_candles(snapshot, config)
    current = candles[-1]
    previous = candles[-2]
    prior_levels = candles[-(config.level_lookback_candles + 1) : -1]
    volume_reference = candles[-(config.volume_lookback_candles + 1) : -1]
    atr = wilder_atr(candles, config.atr_period)
    resistance = max(row["high"] for row in prior_levels)
    support = min(row["low"] for row in prior_levels)
    return_15m = ONE_HUNDRED * (current["close"] / previous["close"] - Decimal("1"))
    volume_percentile = percentile_rank(current["volume"], [row["volume"] for row in volume_reference])
    oi_15 = _change_over(snapshot["open_interest_5m"], "open_interest", 15 * 60_000)
    oi_60 = _change_over(snapshot["open_interest_5m"], "open_interest", 60 * 60_000)
    taker = _taker_window(snapshot["taker_flow_5m"], 3)
    funding = _funding(snapshot["funding_history"], config.funding_history_points)
    market = snapshot["market"]
    spread = _spread_bps(market.get("kraken_bid_price"), market.get("kraken_ask_price"))
    divergence = _divergence_bps(market.get("binance_last_price"), market.get("kraken_last_price"))
    features = {
        "completed_15m": True,
        "completed_candle_open_time_ms": current["open_time_ms"],
        "completed_candle_close_time_ms": current["close_time_ms"],
        "completed_open": current["open"],
        "completed_high": current["high"],
        "completed_low": current["low"],
        "completed_close": current["close"],
        "resistance": resistance,
        "support": support,
        "atr_15m": atr,
        "atr_method": "wilder_rma",
        "return_15m_pct": return_15m,
        "volume_percentile": volume_percentile,
        "oi_change_15m_pct": oi_15,
        "oi_change_60m_pct": oi_60,
        "taker_ratio": taker["ratio"],
        "taker_buy_share": taker["buy_share"],
        "funding_rate": funding["rate"],
        "funding_percentile": funding["percentile"],
        "global_long_account_share": _latest_share(snapshot["positioning"]["global"]),
        "top_long_account_share": _latest_share(snapshot["positioning"]["top_accounts"]),
        "top_long_position_share": _latest_share(snapshot["positioning"]["top_positions"]),
        "spread_bps": spread,
        "cross_venue_divergence_bps": divergence,
        "price_oi_quadrant": price_oi_quadrant(return_15m, oi_15, decimal(config.minimum_material_change_pct)),
    }
    return features


def _completed_candles(snapshot: dict[str, Any], config: ObserverConfig) -> list[dict[str, Any]]:
    exchange_time = int(snapshot["exchange_time_ms"])
    rows = sorted(
        (row for row in snapshot.get("candles_15m", []) if int(row["close_time_ms"]) < exchange_time),
        key=lambda row: int(row["open_time_ms"]),
    )
    required = max(config.volume_lookback_candles + 1, config.level_lookback_candles + 1, config.atr_period + 1)
    if len(rows) < required:
        raise FeatureError(f"insufficient completed candles: need {required}, got {len(rows)}")
    rows = rows[-required:]
    for previous, current in zip(rows, rows[1:]):
        if int(current["open_time_ms"]) - int(previous["open_time_ms"]) != config.interval_ms:
            raise FeatureError("missing or duplicate candle in feature window")
    for row in rows:
        o, h, low, close, volume = (row[key] for key in ("open", "high", "low", "close", "volume"))
        if min(o, h, low, close) <= ZERO or volume < ZERO or not low <= min(o, close) <= max(o, close) <= h:
            raise FeatureError("invalid OHLCV candle")
    return rows


def wilder_atr(candles: list[dict[str, Any]], period: int) -> Decimal:
    if len(candles) < period + 1:
        raise FeatureError("ATR warmup shortfall")
    true_ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    atr = sum(true_ranges[:period], ZERO) / Decimal(period)
    for tr in true_ranges[period:]:
        atr = (atr * Decimal(period - 1) + tr) / Decimal(period)
    return atr


def percentile_rank(value: Decimal, reference: list[Decimal]) -> Decimal:
    if not reference:
        raise FeatureError("percentile reference is empty")
    return Decimal(sum(item <= value for item in reference)) / Decimal(len(reference))


def _change_over(rows: list[dict[str, Any]], field: str, window_ms: int) -> Decimal:
    if len(rows) < 2:
        raise FeatureError("open-interest history is incomplete")
    rows = sorted(rows, key=lambda item: int(item["timestamp_ms"]))
    latest = rows[-1]
    target = int(latest["timestamp_ms"]) - window_ms
    candidates = [row for row in rows[:-1] if int(row["timestamp_ms"]) <= target]
    if not candidates:
        raise FeatureError(f"no open-interest value at or before {window_ms // 60000}m lookback")
    past = candidates[-1][field]
    if past <= ZERO:
        raise FeatureError("open-interest denominator must be positive")
    return ONE_HUNDRED * (latest[field] / past - Decimal("1"))


def _taker_window(rows: list[dict[str, Any]], points: int) -> dict[str, Decimal]:
    if len(rows) < points:
        raise FeatureError("taker-flow history is incomplete")
    window = sorted(rows, key=lambda item: int(item["timestamp_ms"]))[-points:]
    buy = sum((row["buy_volume"] for row in window), ZERO)
    sell = sum((row["sell_volume"] for row in window), ZERO)
    total = buy + sell
    if total <= ZERO or sell <= ZERO:
        raise FeatureError("taker-flow denominator must be positive")
    return {"ratio": buy / sell, "buy_share": buy / total}


def _funding(rows: list[dict[str, Any]], points: int) -> dict[str, Decimal | None]:
    if not rows:
        return {"rate": None, "percentile": None}
    ordered = sorted(rows, key=lambda item: int(item["timestamp_ms"]))
    latest = ordered[-1]["rate"]
    reference = [row["rate"] for row in ordered[-(points + 1) : -1]]
    return {"rate": latest, "percentile": percentile_rank(latest, reference) if reference else None}


def _latest_share(rows: list[dict[str, Any]]) -> Decimal | None:
    return rows[-1]["long_share"] if rows else None


def _spread_bps(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None or bid <= ZERO or ask < bid:
        return None
    midpoint = (bid + ask) / Decimal("2")
    return TEN_THOUSAND * (ask - bid) / midpoint


def _divergence_bps(first: Decimal | None, second: Decimal | None) -> Decimal | None:
    if first is None or second is None or first <= ZERO or second <= ZERO:
        return None
    midpoint = (first + second) / Decimal("2")
    return TEN_THOUSAND * abs(first - second) / midpoint


def price_oi_quadrant(price_change: Decimal, oi_change: Decimal, threshold: Decimal) -> str:
    if abs(price_change) < threshold or abs(oi_change) < threshold:
        return "IMMATERIAL"
    if price_change > ZERO and oi_change > ZERO:
        return "PRICE_UP_OI_UP"
    if price_change > ZERO and oi_change < ZERO:
        return "PRICE_UP_OI_DOWN"
    if price_change < ZERO and oi_change > ZERO:
        return "PRICE_DOWN_OI_UP"
    return "PRICE_DOWN_OI_DOWN"
