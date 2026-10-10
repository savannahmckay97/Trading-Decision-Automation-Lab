from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from market_observer.collectors import PublicCollectors
from market_observer.config import ObserverConfig
from market_observer.http import HttpError, JsonHttpClient
from market_observer.normalization import build_snapshot
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from test_observer import payloads


BASE = "https://fapi.binance.com"


def error(status, body=b'{"code":-1121,"msg":"Invalid symbol"}', retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return HTTPError(BASE, status, "provider error", headers, BytesIO(body))


class Responses:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append(request.full_url)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return BytesIO(value)


class HttpRetryTests(unittest.TestCase):
    def test_deterministic_http_errors_stop_once_and_keep_provider_body(self):
        for status in (400, 401, 403, 404, 451):
            with self.subTest(status=status), patch("market_observer.http.time.sleep") as sleep:
                response = error(status)
                opener = Responses(response)
                with self.assertRaisesRegex(HttpError, "-1121.*Invalid symbol"):
                    JsonHttpClient(opener=opener).get(BASE, "/fapi/v1/ticker/price")
                self.assertEqual(len(opener.calls), 1)
                self.assertTrue(response.fp is None or response.fp.closed)
                sleep.assert_not_called()

    def test_error_body_is_bounded(self):
        opener = Responses(error(400, b'prefix ' + b'x' * 5000))
        with self.assertRaises(HttpError) as caught:
            JsonHttpClient(opener=opener).get(BASE, "/probe")
        self.assertIn("[truncated]", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 1200)

    def test_rate_limits_defer_host_requests_until_retry_after(self):
        for status in (429, 418):
            with self.subTest(status=status), patch("market_observer.http.time.sleep") as sleep:
                clock = [100.0]
                opener = Responses(error(status, retry_after="120"), b'{"ok":true}', b'{"ok":true}')
                client = JsonHttpClient(opener=opener)
                with patch("market_observer.http.time.monotonic", side_effect=lambda: clock[0]):
                    with self.assertRaisesRegex(HttpError, "host cooldown 120s"):
                        client.get(BASE, "/first")
                    clock[0] = 219.0
                    with self.assertRaisesRegex(HttpError, "cooldown"):
                        client.get(BASE, "/different-endpoint")
                    self.assertEqual(len(opener.calls), 1)
                    self.assertTrue(client.get("https://api.kraken.com", "/other-host")["ok"])
                    clock[0] = 220.0
                    self.assertTrue(client.get(BASE, "/after-deadline")["ok"])
                self.assertEqual(len(opener.calls), 3)
                sleep.assert_not_called()

    def test_http_date_retry_after_is_respected(self):
        wall = 2_000_000_000
        date = format_datetime(datetime.fromtimestamp(wall + 90, timezone.utc), usegmt=True)
        with patch("market_observer.http.time.time", return_value=wall), patch("market_observer.http.time.monotonic", return_value=50):
            client = JsonHttpClient(opener=Responses(error(429, retry_after=date)))
            with self.assertRaisesRegex(HttpError, "cooldown 90s"):
                client.get(BASE, "/first")

    def test_invalid_missing_expired_or_zero_retry_after_uses_safe_cooldown(self):
        for header in (None, "", "garbage", "-1", "0", "9" * 129, "Thu, 01 Jan 1970 00:00:00 GMT"):
            with self.subTest(header=header), patch("market_observer.http.time.sleep") as sleep:
                client = JsonHttpClient(opener=Responses(error(429, retry_after=header)))
                with self.assertRaisesRegex(HttpError, "cooldown 60s"):
                    client.get(BASE, "/first")
                sleep.assert_not_called()

    def test_very_long_deadline_does_not_block_worker_with_sleep(self):
        with patch("market_observer.http.time.sleep") as sleep:
            client = JsonHttpClient(opener=Responses(error(418, retry_after="86400")))
            with self.assertRaisesRegex(HttpError, "cooldown 86400s"):
                client.get(BASE, "/first")
            sleep.assert_not_called()

    def test_transient_transport_and_server_errors_retry_with_bounded_backoff(self):
        for failure in (URLError("temporary DNS failure"), error(503, b'service unavailable')):
            with self.subTest(failure=failure), patch("market_observer.http.time.sleep") as sleep:
                opener = Responses(failure, b'{"price":"100"}')
                self.assertEqual(JsonHttpClient(opener=opener).get(BASE, "/probe"), {"price": "100"})
                self.assertEqual(len(opener.calls), 2)
                sleep.assert_called_once_with(1)
        with patch("market_observer.http.time.sleep") as sleep:
            opener = Responses(error(500, b'a'), error(502, b'b'), error(503, b'last body'))
            with self.assertRaisesRegex(HttpError, "3 attempts.*last body"):
                JsonHttpClient(opener=opener).get(BASE, "/probe")
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_declared_provider_errors_and_malformed_json_do_not_retry(self):
        for data in (b'{"code":-1121,"msg":"Invalid symbol"}', b'{"error":["Unknown asset pair"]}', b'not JSON'):
            with self.subTest(data=data), patch("market_observer.http.time.sleep") as sleep:
                opener = Responses(data)
                with self.assertRaises(HttpError):
                    JsonHttpClient(opener=opener).get(BASE, "/probe")
                self.assertEqual(len(opener.calls), 1)
                sleep.assert_not_called()


class EmptySeriesTests(unittest.TestCase):
    def collectors(self, endpoint, replacement):
        data, exchange = payloads("breakout")
        data[endpoint] = replacement
        class PayloadClient:
            def get(self, base, path, params):
                spec = next(spec for spec in collectors._specifications() if spec[3:6] == (base, path, params))
                return data[spec[0]]
        collectors = PublicCollectors(ObserverConfig(), PayloadClient())
        return collectors, exchange

    def test_empty_or_wrong_shape_required_series_are_explicit_collection_errors(self):
        for name in ("binance_taker", "binance_oi_history", "binance_klines"):
            for payload in ([], {}, None):
                with self.subTest(endpoint=name, payload=payload):
                    collectors, clock = self.collectors(name, payload)
                    with patch("market_observer.collectors.now_ms", return_value=clock):
                        events, errors = collectors.collect()
                    self.assertNotIn(name, events)
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0].endpoint, name)
                    self.assertTrue(errors[0].critical)
                    self.assertIn("non-empty array", errors[0].detail)
                    row = build_snapshot(events, errors, "TAOUSDT", "TAOUSD")
                    self.assertIn(name, row["quality"]["critical_missing"])

    def test_empty_required_derivative_still_blocks_observer(self):
        collectors, clock = self.collectors("binance_taker", [])
        with patch("market_observer.collectors.now_ms", return_value=clock), EventStore(":memory:") as store:
            decision = ObserverService(ObserverConfig(), store, collectors).cycle()
        self.assertEqual(decision["signal"], "DATA_STALE")
        self.assertEqual(decision["action"], "NO_ORDER")
        self.assertFalse(decision["setup"]["state_updated"])
        self.assertEqual(decision["collection_errors"][0]["endpoint"], "binance_taker")

    def test_optional_empty_series_remain_successful_unavailable_annotations(self):
        for name in ("binance_funding", "binance_global_ratio"):
            with self.subTest(endpoint=name):
                collectors, clock = self.collectors(name, [])
                with patch("market_observer.collectors.now_ms", return_value=clock):
                    events, errors = collectors.collect()
                self.assertEqual(errors, [])
                self.assertIn(name, events)
                self.assertIsNone(events[name].event_time_ms)


if __name__ == "__main__":
    unittest.main()
