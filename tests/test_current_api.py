import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings  # noqa: E402
from current_api import (  # noqa: E402
    CurrencyAPI,
    CurrencyAPIError,
    MissingKeyError,
    QuotaExceededError,
    RateCache,
    RateResult,
    UsageCounter,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        if not self.responses:
            raise AssertionError("Лишний HTTP-запрос в тесте")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_settings(tmp, provider="exchangerate_host", api_key="KEY", fallback=False, ttl=12):
    return Settings(
        telegram_token="",
        provider=provider,
        api_key=api_key,
        fallback_enabled=fallback,
        cache_ttl_hours=ttl,
        database_path=Path(tmp) / "wallet.db",
        data_dir=Path(tmp),
        project_root=Path(tmp),
    )


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_api(self, session, **settings_kwargs):
        settings = make_settings(self.dir, **settings_kwargs)
        return CurrencyAPI(
            settings,
            session=session,
            cache=RateCache(self.dir / "cache.json", settings.cache_ttl_hours),
            usage=UsageCounter(self.dir / "usage.json"),
        )


class ExchangerateHostTests(ApiTestCase):
    def test_parses_quotes(self):
        session = FakeSession(
            FakeResponse({"success": True, "source": "RUB", "quotes": {"RUBEUR": 0.0096}})
        )
        api = self.make_api(session)
        result = api.get_rate("RUB", "EUR")
        self.assertEqual(result.rate, Decimal("0.0096"))
        self.assertEqual(result.provider, "exchangerate_host")
        self.assertFalse(result.from_cache)
        self.assertEqual(session.calls[0][0], "https://api.exchangerate.host/live")
        self.assertEqual(session.calls[0][1]["source"], "RUB")
        self.assertEqual(session.calls[0][1]["currencies"], "EUR")
        self.assertEqual(session.calls[0][1]["access_key"], "KEY")

    def test_cache_prevents_second_request(self):
        session = FakeSession(
            FakeResponse({"success": True, "quotes": {"RUBEUR": 0.0096}})
        )
        api = self.make_api(session)
        api.get_rate("RUB", "EUR")
        second = api.get_rate("RUB", "EUR")
        self.assertTrue(second.from_cache)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(api.usage.snapshot()["exchangerate_host"], 1)

    def test_same_currency_needs_no_request(self):
        session = FakeSession()
        api = self.make_api(session)
        result = api.get_rate("EUR", "EUR")
        self.assertEqual(result.rate, Decimal("1"))
        self.assertEqual(result.provider, "same")
        self.assertEqual(session.calls, [])

    def test_missing_key_without_fallback(self):
        session = FakeSession()
        api = self.make_api(session, api_key="", fallback=False)
        with self.assertRaises(MissingKeyError):
            api.get_rate("RUB", "EUR")
        self.assertEqual(session.calls, [])
        self.assertEqual(api.usage.snapshot(), {})

    def test_quota_error_mapped(self):
        session = FakeSession(
            FakeResponse({"success": False, "error": {"code": 104, "info": "quota"}})
        )
        api = self.make_api(session)
        with self.assertRaises(QuotaExceededError) as context:
            api.get_rate("RUB", "EUR")
        self.assertIn("лимит", str(context.exception).lower())

    def test_missing_pair(self):
        session = FakeSession(FakeResponse({"success": True, "quotes": {"RUBUSD": 0.01}}))
        api = self.make_api(session)
        with self.assertRaises(CurrencyAPIError):
            api.get_rate("RUB", "EUR")

    def test_invalid_json(self):
        session = FakeSession(FakeResponse(ValueError("bad json")))
        api = self.make_api(session)
        with self.assertRaises(CurrencyAPIError):
            api.get_rate("RUB", "EUR")

    def test_network_error(self):
        session = FakeSession(requests.ConnectionError("no network"))
        api = self.make_api(session)
        with self.assertRaises(CurrencyAPIError) as context:
            api.get_rate("RUB", "EUR")
        self.assertEqual(context.exception.code, "network")

    def test_multiple_rates_one_request(self):
        session = FakeSession(
            FakeResponse({"success": True, "quotes": {"RUBEUR": 0.0096, "RUBGBP": 0.008}})
        )
        api = self.make_api(session)
        rates = api.get_current_rates("RUB", ["EUR", "GBP"])
        self.assertEqual(rates["EUR"], Decimal("0.0096"))
        self.assertEqual(rates["GBP"], Decimal("0.008"))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["currencies"], "EUR,GBP")


class OpenErApiTests(ApiTestCase):
    def test_parses_rates(self):
        session = FakeSession(
            FakeResponse({"result": "success", "base_code": "EUR", "rates": {"RUB": 104.25}})
        )
        api = self.make_api(session, provider="open_er_api", api_key="")
        result = api.get_rate("EUR", "RUB")
        self.assertEqual(result.rate, Decimal("104.25"))
        self.assertEqual(result.provider, "open_er_api")
        self.assertEqual(session.calls[0][0], "https://open.er-api.com/v6/latest/EUR")

    def test_fallback_when_key_missing(self):
        session = FakeSession(
            FakeResponse({"result": "success", "rates": {"CNY": 0.0852}})
        )
        api = self.make_api(session, api_key="", fallback=True)
        result = api.get_rate("RUB", "CNY")
        self.assertTrue(result.fallback)
        self.assertEqual(result.provider, "open_er_api")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(api.usage.snapshot(), {"open_er_api": 1})

    def test_unsupported_currency(self):
        session = FakeSession(FakeResponse({"result": "error", "error-type": "unsupported-code"}))
        api = self.make_api(session, provider="open_er_api", api_key="")
        with self.assertRaises(CurrencyAPIError) as context:
            api.get_rate("RUB", "XYZ")
        self.assertEqual(context.exception.code, "unsupported-code")

    def test_http_429_is_quota(self):
        session = FakeSession(FakeResponse({}, status_code=429))
        api = self.make_api(session, provider="open_er_api", api_key="")
        with self.assertRaises(QuotaExceededError):
            api.get_rate("RUB", "EUR")

    def test_convert(self):
        session = FakeSession(
            FakeResponse({"result": "success", "rates": {"RUB": 104.25}})
        )
        api = self.make_api(session, provider="open_er_api", api_key="")
        self.assertEqual(api.convert(100, "EUR", "RUB"), Decimal("10425.00"))


class ValidationTests(ApiTestCase):
    def test_invalid_currency_code(self):
        api = self.make_api(FakeSession())
        with self.assertRaises(CurrencyAPIError):
            api.get_rate("EURO", "RUB")

    def test_convert_rejects_non_positive(self):
        api = self.make_api(FakeSession())
        with self.assertRaises(CurrencyAPIError):
            api.convert(0, "EUR", "RUB")


class CacheTests(ApiTestCase):
    def test_ttl_zero_disables_cache(self):
        cache = RateCache(self.dir / "cache.json", 0)
        cache.put(
            RateResult("EUR", "RUB", Decimal("100"), "open_er_api", datetime.now(timezone.utc))
        )
        self.assertIsNone(cache.get("open_er_api", "EUR", "RUB"))

    def test_expired_entry_is_ignored(self):
        cache = RateCache(self.dir / "cache.json", 12)
        old = datetime.now(timezone.utc) - timedelta(hours=13)
        cache.put(RateResult("EUR", "RUB", Decimal("100"), "open_er_api", old))
        self.assertIsNone(cache.get("open_er_api", "EUR", "RUB"))

    def test_fresh_entry_is_returned(self):
        cache = RateCache(self.dir / "cache.json", 12)
        cache.put(
            RateResult("EUR", "RUB", Decimal("100"), "open_er_api", datetime.now(timezone.utc))
        )
        result = cache.get("open_er_api", "EUR", "RUB")
        self.assertIsNotNone(result)
        self.assertTrue(result.from_cache)


if __name__ == "__main__":
    unittest.main()
