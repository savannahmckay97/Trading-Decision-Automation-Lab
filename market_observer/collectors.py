"""Public REST collectors. No authenticated or order endpoints exist here."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from .config import ObserverConfig
from .http import JsonHttpClient
from .util import now_ms, stable_id


@dataclass(frozen=True)
class RawEvent:
    source: str
    endpoint: str
    symbol: str
    event_time_ms: int | None
    received_time_ms: int
    payload: Any
    critical: bool

    @property
    def raw_id(self) -> str:
        return stable_id(
            "raw",
            {
                "source": self.source,
                "endpoint": self.endpoint,
                "symbol": self.symbol,
                "received_time_ms": self.received_time_ms,
                "payload": self.payload,
            },
        )

    def record(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "source": self.source,
            "endpoint": self.endpoint,
            "symbol": self.symbol,
            "event_time_ms": self.event_time_ms,
            "received_time_ms": self.received_time_ms,
            "critical": self.critical,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class CollectionError:
    source: str
    endpoint: str
    symbol: str
    critical: bool
    received_time_ms: int
    detail: str

    def record(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PublicCollectors:
    BINANCE_BASE = "https://fapi.binance.com"
    KRAKEN_BASE = "https://api.kraken.com"

    def __init__(self, config: ObserverConfig, client: JsonHttpClient | None = None) -> None:
        self.config = config
        self.client = client or JsonHttpClient(config.request_timeout_seconds, config.request_attempts)
        self.completed_at_ms: int | None = None

    def collect(self) -> tuple[dict[str, RawEvent], list[CollectionError]]:
        self.completed_at_ms = None
        specs = self._specifications()
        events: dict[str, RawEvent] = {}
        errors: list[CollectionError] = []
        with ThreadPoolExecutor(max_workers=min(8, len(specs)), thread_name_prefix="market-fetch") as pool:
            futures = {pool.submit(self._fetch_one, *spec): spec for spec in specs}
            for future in as_completed(futures):
                name, source, symbol, _base, _path, _params, critical = futures[future]
                try:
                    events[name] = future.result()
                except Exception as exc:
                    errors.append(
                        CollectionError(
                            source=source,
                            endpoint=name,
                            symbol=symbol,
                            critical=critical,
                            received_time_ms=now_ms(),
                            detail=f"{type(exc).__name__}: {exc}",
                        )
                    )
        # Every request (including optional failures/retries) has now finished.
        self.completed_at_ms = now_ms()
        return events, sorted(errors, key=lambda item: item.endpoint)

    def _specifications(self) -> list[tuple]:
        s = self.config.research_symbol
        pair = self.config.executable_pair
        common = {"symbol": s}
        return [
            ("binance_time", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/time", {}, True),
            ("binance_price", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/ticker/price", common, True),
            ("binance_book", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/ticker/bookTicker", common, False),
            ("binance_klines", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/klines", {**common, "interval": self.config.interval, "limit": self.config.candle_limit}, True),
            ("binance_oi", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/openInterest", common, False),
            ("binance_oi_history", "binance_usds_m_futures", s, self.BINANCE_BASE, "/futures/data/openInterestHist", {**common, "period": "5m", "limit": 30}, True),
            ("binance_taker", "binance_usds_m_futures", s, self.BINANCE_BASE, "/futures/data/takerlongshortRatio", {**common, "period": "5m", "limit": 30}, True),
            ("binance_funding", "binance_usds_m_futures", s, self.BINANCE_BASE, "/fapi/v1/fundingRate", {**common, "limit": 100}, False),
            ("binance_global_ratio", "binance_usds_m_futures", s, self.BINANCE_BASE, "/futures/data/globalLongShortAccountRatio", {**common, "period": "5m", "limit": 2}, False),
            ("binance_top_account_ratio", "binance_usds_m_futures", s, self.BINANCE_BASE, "/futures/data/topLongShortAccountRatio", {**common, "period": "5m", "limit": 2}, False),
            ("binance_top_position_ratio", "binance_usds_m_futures", s, self.BINANCE_BASE, "/futures/data/topLongShortPositionRatio", {**common, "period": "5m", "limit": 2}, False),
            ("kraken_ticker", "kraken_spot", pair, self.KRAKEN_BASE, "/0/public/Ticker", {"pair": pair}, True),
        ]

    def _fetch_one(
        self,
        name: str,
        source: str,
        symbol: str,
        base: str,
        path: str,
        params: dict[str, Any],
        critical: bool,
    ) -> RawEvent:
        payload = self.client.get(base, path, params)
        if critical and name in {"binance_klines", "binance_oi_history", "binance_taker"}:
            if not isinstance(payload, list) or not payload:
                raise ValueError(f"{name}: required series must be a non-empty array")
        mismatch = payload_identity_error(name, payload, symbol)
        if mismatch:
            raise ValueError(f"{name}: {mismatch}")
        received = now_ms()
        return RawEvent(source, name, symbol, _event_time(name, payload, received), received, payload, critical)


# Kraken's own asset codes for bases whose public ticker differs (BTC is XBT, DOGE is XDG).
KRAKEN_ASSET_CODES = {"BTC": "XBT", "DOGE": "XDG"}
# Binance endpoints whose responses always name the instrument. Klines and the taker
# ratio carry no symbol, so their identity is bound to the request only.
SYMBOL_REQUIRED = frozenset({
    "binance_price", "binance_book", "binance_oi", "binance_oi_history", "binance_funding",
    "binance_global_ratio", "binance_top_account_ratio", "binance_top_position_ratio",
})


def kraken_result_keys(pair: str) -> set[str]:
    """Result keys Kraken may return for a requested fiat pair such as BTCUSD: the pair,
    its Kraken-code altname (XBTUSD, XDGUSD) and the legacy X<code>Z<quote> form
    (XXBTZUSD). Assumes a three-letter fiat quote, as the executable USD pair has."""
    base, quote = pair[:-3], pair[-3:]
    code = KRAKEN_ASSET_CODES.get(base, base)
    return {pair, f"{code}{quote}", f"X{code}Z{quote}"}


def payload_identity_error(name: str, payload: Any, expected_symbol: str) -> str | None:
    """Return why a provider payload doesn't identify the expected instrument, or None.

    Endpoints that always name their instrument must name the expected one on every
    row; a missing symbol is a mismatch there. Klines and the taker ratio carry no
    symbol and are bound to the request only.
    """
    if name == "kraken_ticker":
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or len(result) != 1:
            return f"expected exactly one Kraken result, got {sorted(result) if isinstance(result, dict) else result!r}"
        (key,) = result
        return None if key in kraken_result_keys(expected_symbol) else f"Kraken result {key!r} is not {expected_symbol}"
    if name not in SYMBOL_REQUIRED:
        return None
    if payload == []:
        return None  # an empty series has nothing to misattribute; emptiness is handled elsewhere
    rows = payload if isinstance(payload, list) else [payload]
    symbols = [row.get("symbol") if isinstance(row, dict) else None for row in rows]
    if any(symbol != expected_symbol for symbol in symbols):
        return f"payload symbols {sorted(set(map(str, symbols)))} are not all {expected_symbol}"
    return None


def _event_time(name: str, payload: Any, fallback_ms: int) -> int | None:
    try:
        if name == "binance_time":
            return int(payload["serverTime"])
        if name in {"binance_price", "binance_book", "binance_oi"}:
            return int(payload.get("time", fallback_ms))
        if name == "binance_klines":
            return max(int(row[6]) for row in payload)
        if name in {"binance_oi_history", "binance_taker", "binance_global_ratio", "binance_top_account_ratio", "binance_top_position_ratio"}:
            return max(int(row["timestamp"]) for row in payload)
        if name == "binance_funding":
            return max(int(row["fundingTime"]) for row in payload)
        if name == "kraken_ticker":
            return fallback_ms  # Kraken ticker REST has no provider event timestamp.
    except (KeyError, TypeError, ValueError):
        return None
    return fallback_ms
