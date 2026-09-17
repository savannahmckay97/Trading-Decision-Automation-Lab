"""Command-line runner for one cycle or a supervised polling loop."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .collectors import PublicCollectors
from .config import ObserverConfig
from .http import JsonHttpClient
from .service import ObserverService
from .storage import EventStore, default_database_path
from .util import json_default


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Trading Decision Automation Lab — observation compartment")
    result.add_argument("--base", default="TAO", help="base asset; TAO by default")
    result.add_argument("--db", default=str(default_database_path()), help="durable SQLite path")
    result.add_argument("--loop", action="store_true", help="continue polling until interrupted")
    result.add_argument("--poll-seconds", type=int, default=60, help="poll interval; minimum 30")
    result.add_argument("--pretty", action="store_true", help="indent JSON output")
    result.add_argument("--request-attempts", type=int, default=3)
    result.add_argument("--request-timeout", type=float, default=12.0)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = ObserverConfig(
        base_asset=args.base.upper(),
        poll_seconds=args.poll_seconds,
        request_attempts=args.request_attempts,
        request_timeout_seconds=args.request_timeout,
    )
    try:
        config.validate()
    except ValueError as exc:
        parser().error(str(exc))
    client = JsonHttpClient(config.request_timeout_seconds, config.request_attempts)
    with EventStore(Path(args.db)) as store:
        service = ObserverService(config, store, PublicCollectors(config, client))
        try:
            while True:
                started = time.monotonic()
                decision = service.cycle()
                print(
                    json.dumps(
                        decision,
                        default=json_default,
                        sort_keys=True,
                        indent=2 if args.pretty else None,
                    ),
                    flush=True,
                )
                if not args.loop:
                    break
                remaining = max(0.0, config.poll_seconds - (time.monotonic() - started))
                time.sleep(remaining)
        except KeyboardInterrupt:
            print("observer stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
