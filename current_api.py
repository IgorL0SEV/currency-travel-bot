"""Курсы валют из двух сервисов: exchangerate.host и open.er-api.com.

Провайдер выбирается в ``.env`` (``CURRENCY_PROVIDER``). Если основной
сервис недоступен, можно разрешить аварийный переход на второй
(``CURRENCY_FALLBACK=1``).

Каждый запрос учитывается в локальном счётчике ``api_usage.json``:
у бесплатного тарифа exchangerate.host лимит 100 запросов в месяц.

Курс всегда означает «сколько единиц quote за 1 единицу base».
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Protocol

import requests

from config import (
    PROVIDER_EXCHANGERATE,
    PROVIDER_OPEN_ER_API,
    Settings,
    load_settings,
)
from money import rate_value, to_decimal

log = logging.getLogger(__name__)

PROVIDER_TITLES = {
    PROVIDER_EXCHANGERATE: "exchangerate.host",
    PROVIDER_OPEN_ER_API: "open.er-api.com",
}

EXCHANGERATE_LIVE_URL = "https://api.exchangerate.host/live"
OPEN_ER_API_URL = "https://open.er-api.com/v6/latest/{base}"
DEFAULT_TIMEOUT = (5, 20)
_CODE_RE = re.compile(r"^[A-Z]{3}$")


class CurrencyAPIError(RuntimeError):
    """Понятная пользователю ошибка получения курса."""

    def __init__(self, message: str, *, provider: str | None = None, code: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.code = code


class MissingKeyError(CurrencyAPIError):
    """Для выбранного провайдера нет ключа."""


class QuotaExceededError(CurrencyAPIError):
    """Исчерпан бесплатный лимит запросов."""


@dataclass(frozen=True)
class RateResult:
    base: str
    quote: str
    rate: Decimal
    provider: str
    fetched_at: datetime
    from_cache: bool = False
    fallback: bool = False

    @property
    def provider_title(self) -> str:
        if self.provider == "same":
            return "одинаковые валюты"
        return PROVIDER_TITLES.get(self.provider, self.provider)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_code(code: str) -> str:
    value = (code or "").strip().upper()
    if not _CODE_RE.match(value):
        raise CurrencyAPIError(
            f"Некорректный код валюты: {code!r}. Нужен трёхбуквенный код, например EUR."
        )
    return value


class RateCache:
    """Небольшой файловый кэш курсов: экономит лимит запросов."""

    def __init__(self, path: Path, ttl_hours: float):
        self.path = Path(path)
        self.ttl = timedelta(hours=ttl_hours)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _key(provider: str, base: str, quote: str) -> str:
        return f"{provider}:{base}:{quote}"

    def get(self, provider: str, base: str, quote: str) -> RateResult | None:
        if self.ttl <= timedelta(0):
            return None
        with self._lock:
            entry = self._load().get(self._key(provider, base, quote))
        if not isinstance(entry, dict):
            return None
        try:
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if _utcnow() - fetched_at > self.ttl:
                return None
            rate = to_decimal(entry["rate"])
        except (KeyError, ValueError, InvalidOperation):
            return None
        if rate <= 0:
            return None
        return RateResult(
            base=base,
            quote=quote,
            rate=rate_value(rate),
            provider=provider,
            fetched_at=fetched_at,
            from_cache=True,
        )

    def put(self, result: RateResult) -> None:
        with self._lock:
            data = self._load()
            data[self._key(result.provider, result.base, result.quote)] = {
                "rate": str(result.rate),
                "fetched_at": result.fetched_at.isoformat(timespec="seconds"),
            }
            # Не даём файлу расти бесконечно.
            if len(data) > 500:
                data = dict(list(data.items())[-400:])
            self._save(data)

    def count(self) -> int:
        with self._lock:
            return len(self._load())


class UsageCounter:
    """Сколько запросов к каждому сервису сделано в текущем месяце."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def increment(self, provider: str) -> None:
        month = _utcnow().strftime("%Y-%m")
        with self._lock:
            data = self._load()
            months = data.setdefault("months", {})
            counters = months.setdefault(month, {})
            counters[provider] = int(counters.get(provider, 0)) + 1
            # Хватит двух последних месяцев.
            for old in sorted(months)[:-2]:
                months.pop(old, None)
            self._save(data)

    def snapshot(self) -> dict[str, int]:
        month = _utcnow().strftime("%Y-%m")
        with self._lock:
            months = self._load().get("months", {})
            counters = months.get(month, {})
        return {str(k): int(v) for k, v in counters.items() if isinstance(v, int)}

    def month_total(self) -> int:
        return sum(self.snapshot().values())


class _Session(Protocol):
    def get(self, url: str, **kwargs): ...


class _Provider:
    key: str

    def fetch_rate(self, base: str, quote: str, session: _Session) -> Decimal:
        raise NotImplementedError

    def fetch_rates(self, base: str, quotes: Iterable[str], session: _Session) -> dict[str, Decimal]:
        return {quote: self.fetch_rate(base, quote, session) for quote in quotes}

    @staticmethod
    def _json(response, provider: str) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise CurrencyAPIError(
                f"{PROVIDER_TITLES.get(provider, provider)} вернул ответ, который не удалось разобрать.",
                provider=provider,
            ) from exc
        if not isinstance(data, dict):
            raise CurrencyAPIError(
                f"{PROVIDER_TITLES.get(provider, provider)} вернул неожиданный формат ответа.",
                provider=provider,
            )
        return data


class ExchangerateHostProvider(_Provider):
    key = PROVIDER_EXCHANGERATE

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _require_key(self) -> str:
        if not self.api_key:
            raise MissingKeyError(
                "Не задан CURRENCY_API_KEY для exchangerate.host. Добавьте ключ в .env "
                "или выберите CURRENCY_PROVIDER=open_er_api (этот сервис работает без ключа).",
                provider=self.key,
                code="missing_key",
            )
        return self.api_key

    def fetch_rate(self, base: str, quote: str, session: _Session) -> Decimal:
        rates = self.fetch_rates(base, [quote], session)
        return rates[quote]

    def fetch_rates(self, base: str, quotes: Iterable[str], session: _Session) -> dict[str, Decimal]:
        key = self._require_key()
        ordered = tuple(dict.fromkeys(quotes))
        symbols = ",".join(ordered)
        params = {"access_key": key, "source": base, "currencies": symbols}
        try:
            response = session.get(EXCHANGERATE_LIVE_URL, params=params, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise CurrencyAPIError(
                "Нет связи с exchangerate.host. Проверьте интернет и повторите попытку.",
                provider=self.key,
                code="network",
            ) from exc

        if response.status_code == 429:
            raise QuotaExceededError(
                "exchangerate.host сообщает, что лимит запросов исчерпан. "
                "Подождите восстановления квоты или переключитесь на open_er_api.",
                provider=self.key,
                code="quota",
            )
        if response.status_code in (401, 403):
            raise MissingKeyError(
                "exchangerate.host отклонил ключ. Проверьте CURRENCY_API_KEY в .env.",
                provider=self.key,
                code="auth",
            )

        data = self._json(response, self.key)
        if not data.get("success", True):
            error = data.get("error") or {}
            code = error.get("code")
            info = str(error.get("info") or "").strip()
            mapping = {
                101: ("missing_key", "Не задан ключ exchangerate.host."),
                102: ("inactive_key", "Ключ exchangerate.host неактивен."),
                103: ("invalid_key", "exchangerate.host считает ключ неверным."),
                104: ("quota", "Лимит бесплатных запросов exchangerate.host исчерпан (100 в месяц)."),
                105: ("restricted", "Тариф exchangerate.host не разрешает этот запрос."),
                106: ("no_result", "Сервис не нашёл курс для этой пары валют."),
                201: ("bad_base", "Неподдерживаемая базовая валюта."),
                202: ("bad_currency", "Неподдерживаемый код валюты."),
            }
            kind, message = mapping.get(code, ("api_error", "exchangerate.host вернул ошибку."))
            suffix = f" Детали сервиса: {info}" if info else ""
            error_type = QuotaExceededError if kind == "quota" else (
                MissingKeyError if kind in {"missing_key", "invalid_key", "inactive_key"} else CurrencyAPIError
            )
            raise error_type(f"{message}{suffix}", provider=self.key, code=kind)

        quotes_data = data.get("quotes")
        if not isinstance(quotes_data, dict):
            raise CurrencyAPIError(
                "exchangerate.host не вернул блок курсов.",
                provider=self.key,
                code="empty",
            )
        result: dict[str, Decimal] = {}
        for quote in ordered:
            raw = quotes_data.get(f"{base}{quote}")
            if raw is None:
                raise CurrencyAPIError(
                    f"exchangerate.host не вернул курс {base}/{quote}.",
                    provider=self.key,
                    code="missing_pair",
                )
            try:
                value = to_decimal(raw)
            except (InvalidOperation, ValueError) as exc:
                raise CurrencyAPIError(
                    f"exchangerate.host вернул некорректный курс {base}/{quote}.",
                    provider=self.key,
                    code="bad_rate",
                ) from exc
            if value <= 0:
                raise CurrencyAPIError(
                    f"exchangerate.host вернул нулевой курс {base}/{quote}.",
                    provider=self.key,
                    code="bad_rate",
                )
            result[quote] = rate_value(value)
        return result


class OpenErApiProvider(_Provider):
    key = PROVIDER_OPEN_ER_API

    def fetch_rate(self, base: str, quote: str, session: _Session) -> Decimal:
        return self.fetch_rates(base, [quote], session)[quote]

    def fetch_rates(self, base: str, quotes: Iterable[str], session: _Session) -> dict[str, Decimal]:
        ordered = tuple(dict.fromkeys(quotes))
        try:
            response = session.get(
                OPEN_ER_API_URL.format(base=base), timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise CurrencyAPIError(
                "Нет связи с open.er-api.com. Проверьте интернет и повторите попытку.",
                provider=self.key,
                code="network",
            ) from exc

        if response.status_code == 429:
            raise QuotaExceededError(
                "open.er-api.com временно ограничил запросы. Повторите позже.",
                provider=self.key,
                code="quota",
            )

        data = self._json(response, self.key)
        if data.get("result") != "success":
            error_type = str(data.get("error-type") or "unknown")
            mapping = {
                "unsupported-code": "open.er-api.com не поддерживает эту валюту.",
                "malformed-request": "open.er-api.com не принял запрос.",
                "invalid-key": "open.er-api.com отклонил ключ (для бесплатного тарифа ключ не нужен).",
                "quota-reached": "Лимит запросов open.er-api.com исчерпан.",
            }
            message = mapping.get(error_type, "open.er-api.com вернул ошибку.")
            error_cls = QuotaExceededError if error_type == "quota-reached" else CurrencyAPIError
            raise error_cls(f"{message} ({error_type})", provider=self.key, code=error_type)

        rates = data.get("rates")
        if not isinstance(rates, dict):
            raise CurrencyAPIError(
                "open.er-api.com не вернул блок курсов.",
                provider=self.key,
                code="empty",
            )

        result: dict[str, Decimal] = {}
        for quote in ordered:
            raw = rates.get(quote)
            if raw is None:
                raise CurrencyAPIError(
                    f"open.er-api.com не поддерживает валюту {quote}.",
                    provider=self.key,
                    code="missing_pair",
                )
            try:
                value = to_decimal(raw)
            except (InvalidOperation, ValueError) as exc:
                raise CurrencyAPIError(
                    f"open.er-api.com вернул некорректный курс {base}/{quote}.",
                    provider=self.key,
                    code="bad_rate",
                ) from exc
            if value <= 0:
                raise CurrencyAPIError(
                    f"open.er-api.com вернул нулевой курс {base}/{quote}.",
                    provider=self.key,
                    code="bad_rate",
                )
            result[quote] = rate_value(value)
        return result


class CurrencyAPI:
    """Единая точка получения курсов и конвертации."""

    def __init__(
        self,
        settings: Settings | None = None,
        session: _Session | None = None,
        cache: RateCache | None = None,
        usage: UsageCounter | None = None,
    ):
        self.settings = settings or load_settings()
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update({"User-Agent": "currency-travel-bot/1.0"})
        self.cache = cache or RateCache(self.settings.data_dir / "rate_cache.json", self.settings.cache_ttl_hours)
        self.usage = usage or UsageCounter(self.settings.data_dir / "api_usage.json")
        self.providers: dict[str, _Provider] = {
            PROVIDER_EXCHANGERATE: ExchangerateHostProvider(self.settings.api_key),
            PROVIDER_OPEN_ER_API: OpenErApiProvider(),
        }

    def get_rate(
        self,
        base: str,
        quote: str,
        *,
        provider: str | None = None,
        use_cache: bool = True,
    ) -> RateResult:
        base = _normalize_code(base)
        quote = _normalize_code(quote)
        if base == quote:
            return RateResult(base, quote, Decimal("1"), "same", _utcnow(), from_cache=True)

        provider_key = provider or self.settings.provider
        if provider_key not in self.providers:
            raise CurrencyAPIError(f"Неизвестный провайдер: {provider_key}")

        if use_cache:
            cached = self.cache.get(provider_key, base, quote)
            if cached is not None:
                log.info("Курс %s/%s взят из кэша (%s)", base, quote, provider_key)
                return cached

        try:
            rate = self._fetch(provider_key, base, quote)
            return self._remember(base, quote, rate, provider_key)
        except CurrencyAPIError as primary_error:
            if not (self.settings.fallback_enabled and provider_key == PROVIDER_EXCHANGERATE):
                raise
            fallback_key = PROVIDER_OPEN_ER_API
            log.warning(
                "Основной провайдер %s не сработал (%s), пробуем %s",
                provider_key,
                primary_error,
                fallback_key,
            )
            if use_cache:
                cached = self.cache.get(fallback_key, base, quote)
                if cached is not None:
                    return RateResult(
                        base,
                        quote,
                        cached.rate,
                        fallback_key,
                        cached.fetched_at,
                        from_cache=True,
                        fallback=True,
                    )
            try:
                rate = self._fetch(fallback_key, base, quote)
            except CurrencyAPIError as second_error:
                raise CurrencyAPIError(
                    "Не удалось получить курс ни от exchangerate.host, ни от резервного сервиса. "
                    f"exchangerate.host: {primary_error}. open.er-api.com: {second_error}",
                    provider=provider_key,
                    code="both_failed",
                ) from second_error
            result = self._remember(base, quote, rate, fallback_key)
            return RateResult(
                result.base,
                result.quote,
                result.rate,
                result.provider,
                result.fetched_at,
                from_cache=result.from_cache,
                fallback=True,
            )

    def _fetch(self, provider_key: str, base: str, quote: str) -> Decimal:
        provider = self.providers[provider_key]
        if provider_key == PROVIDER_EXCHANGERATE:
            # Без ключа запрос даже не уходит — не увеличиваем счётчик.
            provider._require_key()  # type: ignore[attr-defined]
        self.usage.increment(provider_key)
        return provider.fetch_rate(base, quote, self.session)

    def _remember(self, base: str, quote: str, rate: Decimal, provider_key: str) -> RateResult:
        result = RateResult(
            base=base,
            quote=quote,
            rate=rate_value(rate),
            provider=provider_key,
            fetched_at=_utcnow(),
        )
        self.cache.put(result)
        return result

    def convert(
        self,
        amount: Decimal | int | float | str,
        from_currency: str,
        to_currency: str,
        **kwargs,
    ) -> Decimal:
        value = to_decimal(amount)
        if value <= 0:
            raise CurrencyAPIError("Сумма для конвертации должна быть положительной.")
        result = self.get_rate(from_currency, to_currency, **kwargs)
        return value * result.rate

    def get_current_rates(
        self,
        default: str = "USD",
        currencies: Iterable[str] = ("EUR", "GBP", "JPY"),
        *,
        provider: str | None = None,
    ) -> dict[str, Decimal]:
        """Курсы нескольких валют одним запросом (функция из задания)."""
        base = _normalize_code(default)
        quotes = [code for code in dict.fromkeys(_normalize_code(c) for c in currencies) if code != base]
        if not quotes:
            return {}
        provider_key = provider or self.settings.provider
        rates = self.providers[provider_key].fetch_rates(base, quotes, self.session)
        self.usage.increment(provider_key)
        return rates

    def status(self) -> dict:
        usage = self.usage.snapshot()
        return {
            "provider": self.settings.provider,
            "provider_title": PROVIDER_TITLES.get(self.settings.provider, self.settings.provider),
            "api_key_present": bool(self.settings.api_key),
            "fallback_enabled": self.settings.fallback_enabled,
            "cache_entries": self.cache.count(),
            "cache_ttl_hours": self.settings.cache_ttl_hours,
            "usage": usage,
            "usage_total": sum(usage.values()),
        }


_default_api: CurrencyAPI | None = None


def get_api(settings: Settings | None = None) -> CurrencyAPI:
    """Ленивый общий экземпляр для бота и демо."""
    global _default_api
    if _default_api is None or settings is not None:
        _default_api = CurrencyAPI(settings)
    return _default_api


def get_exchange_rate(base: str, quote: str, **kwargs) -> RateResult:
    """Публичная функция: сколько ``quote`` за 1 ``base``."""
    return get_api().get_rate(base, quote, **kwargs)


def convert_currency(amount, from_currency: str, to_currency: str, **kwargs) -> Decimal:
    """Публичная функция конвертации суммы."""
    return get_api().convert(amount, from_currency, to_currency, **kwargs)


def get_current_rate(default: str = "USD", currencies: list[str] | tuple[str, ...] = ("EUR", "GBP", "JPY")):
    """Публичная функция из задания: набор курсов от базовой валюты."""
    return get_api().get_current_rates(default, currencies)
