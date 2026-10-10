"""Read-only Binance/Kraken probe for issue #7; saves raw evidence locally.

Run from the repository root: python3 -m scripts.verify_live_derivatives /tmp/tdal-issue7
No credentials, order endpoints, or production state are used.
"""
from __future__ import annotations

from copy import deepcopy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from market_observer.collectors import PublicCollectors
from market_observer.config import ObserverConfig
from market_observer.evaluator import evaluate_snapshot
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayError, ReplayRunner
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from market_observer.util import canonical_json


def main() -> None:
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    config = ObserverConfig()
    events, errors = PublicCollectors(config).collect()
    snapshot = build_snapshot(events, errors, config.research_symbol, config.executable_pair)
    summary = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "collection_errors": [error.record() for error in errors]}
    try:
        result = evaluate_snapshot(snapshot, config, None)
        boundary = result.completed_candle_close_time_ms + 1
        summary.update(signal=result.signal, gates=result.gates,
                       features=result.features, action="NO_ORDER",
                       completed_boundary_ms=boundary,
                       required_taker_opens_ms=list(range(boundary - 900_000, boundary, 300_000)),
                       required_oi_boundaries_ms=[boundary - 3_600_000, boundary - 900_000, boundary],
                       newest_event_ages_ms={name: snapshot["observed_at_ms"] - events[name].event_time_ms
                                             for name in ("binance_taker", "binance_oi_history")})
        try:
            replay = ReplayRunner(config).run([snapshot])["decisions"][0]
            summary["replay_features_match"] = replay["features"] == result.features
        except ReplayError as exc:
            summary["replay_features_match"] = False
            summary["replay_error"] = str(exc)
        class CollectedOnce:
            def collect(self):
                return events, errors
        with EventStore(":memory:") as store:
            decision = ObserverService(config, store, CollectedOnce()).cycle()
            summary["observer_signal"] = decision["signal"]
            summary["execution_permission"] = decision["execution_permission"]
        blocked = {}
        for name in ("taker_flow_5m", "open_interest_5m"):
            incomplete = deepcopy(snapshot)
            timestamp = boundary - 300_000 if name == "taker_flow_5m" else boundary
            incomplete[name] = [row for row in incomplete[name] if row["timestamp_ms"] != timestamp]
            try:
                evaluate_snapshot(incomplete, config, None)
                blocked[name] = False
            except ValueError:
                blocked[name] = True
        summary["missing_required_row_blocks"] = blocked
        # Independently compare taker bucket volumes with Binance 5m klines.
        klines = PublicCollectors(config).client.get(PublicCollectors.BINANCE_BASE,
                    "/fapi/v1/klines", {"symbol": config.research_symbol, "interval": "5m",
                    "startTime": boundary - 900_000, "endTime": boundary - 1, "limit": 3})
        (directory / "klines_5m.json").write_text(canonical_json(klines) + "\n")
        from decimal import Decimal
        taker = {row["timestamp_ms"]: row for row in snapshot["taker_flow_5m"]}
        checks = []
        for candle in klines:
            flow = taker[int(candle[0])]
            checks.append({"open_time_ms": candle[0],
                           "buy_volume_difference": flow["buy_volume"] - Decimal(candle[9]),
                           "total_volume_difference": flow["buy_volume"] + flow["sell_volume"] - Decimal(candle[5]),
                           "buy_matches": flow["buy_volume"] == Decimal(candle[9]),
                           "total_matches": flow["buy_volume"] + flow["sell_volume"] == Decimal(candle[5])})
        summary["taker_kline_checks"] = checks
        summary["taker_open_semantics_verified"] = len(checks) == 3 and all(
            row["buy_matches"] and row["total_matches"] for row in checks)
    except (ValueError, KeyError, TypeError) as exc:
        summary.update(signal="DATA_STALE", detail=str(exc), action="NO_ORDER")
    (directory / "raw_events.json").write_text(canonical_json([event.record() for event in events.values()]) + "\n")
    (directory / "snapshot.json").write_text(canonical_json(snapshot) + "\n")
    (directory / "summary.json").write_text(canonical_json(summary) + "\n")
    print(json.dumps(json.loads(canonical_json(summary)), indent=2))


if __name__ == "__main__":
    main()
