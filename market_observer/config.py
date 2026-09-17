"""Versioned configuration with deliberately conservative defaults."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ObserverConfig:
    base_asset: str = "TAO"
    research_quote: str = "USDT"
    executable_quote: str = "USD"
    interval: str = "15m"
    interval_ms: int = 900_000
    candle_limit: int = 150
    poll_seconds: int = 60
    request_timeout_seconds: float = 12.0
    request_attempts: int = 3
    level_lookback_candles: int = 32
    atr_period: int = 14
    volume_lookback_candles: int = 96
    funding_history_points: int = 90
    minimum_material_change_pct: float = 0.02
    max_cross_venue_divergence_bps: float = 35.0
    max_spread_bps: float = 15.0
    breakout_buffer_atr: float = 0.10
    retest_tolerance_atr: float = 0.20
    failure_buffer_atr: float = 0.15
    minimum_breakout_quality: int = 1
    minimum_retest_buy_share: float = 0.50
    expiry_bars: int = 6
    mode: str = "observation_only"

    @property
    def research_symbol(self) -> str:
        return f"{self.base_asset.upper()}{self.research_quote.upper()}"

    @property
    def executable_pair(self) -> str:
        return f"{self.base_asset.upper()}{self.executable_quote.upper()}"

    def validate(self) -> None:
        if self.mode not in {"observation_only", "historical_replay", "paper"}:
            raise ValueError("project-zero v0.1 forbids live execution modes")
        if self.poll_seconds < 30:
            raise ValueError("poll_seconds must be at least 30")
        if self.candle_limit < max(self.volume_lookback_candles + 2, self.level_lookback_candles + 2):
            raise ValueError("candle_limit is too small for configured lookbacks")
        if self.atr_period < 2 or self.expiry_bars < 1:
            raise ValueError("invalid ATR period or expiry")
        if not 0 <= self.minimum_retest_buy_share <= 1:
            raise ValueError("minimum_retest_buy_share must be between zero and one")
        if min(self.max_cross_venue_divergence_bps, self.max_spread_bps) <= 0:
            raise ValueError("spread and divergence limits must be positive")

    def public_dict(self) -> dict:
        data = asdict(self)
        data.update(research_symbol=self.research_symbol, executable_pair=self.executable_pair)
        return data
