"""Загрузка настроек из .env и проверка обязательных значений."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

PROVIDER_EXCHANGERATE = "exchangerate_host"
PROVIDER_OPEN_ER_API = "open_er_api"
SUPPORTED_PROVIDERS = (PROVIDER_EXCHANGERATE, PROVIDER_OPEN_ER_API)

_PROVIDER_ALIASES = {
    "exchangerate": PROVIDER_EXCHANGERATE,
    "exchangerate_host": PROVIDER_EXCHANGERATE,
    "exchangeratehost": PROVIDER_EXCHANGERATE,
    "exchangerate.host": PROVIDER_EXCHANGERATE,
    "open_er_api": PROVIDER_OPEN_ER_API,
    "openerapi": PROVIDER_OPEN_ER_API,
    "open.er-api.com": PROVIDER_OPEN_ER_API,
    "open-er-api": PROVIDER_OPEN_ER_API,
}


class ConfigError(RuntimeError):
    """Настройки заполнены неверно — пользователю нужно объяснить, что поправить."""


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    provider: str
    api_key: str
    fallback_enabled: bool
    cache_ttl_hours: float
    database_path: Path
    data_dir: Path
    project_root: Path

    @property
    def provider_title(self) -> str:
        return "exchangerate.host" if self.provider == PROVIDER_EXCHANGERATE else "open.er-api.com"


def _read_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _read_ttl(value: str | None, default: float = 12.0) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        ttl = float(value)
    except ValueError as exc:
        raise ConfigError(
            f"RATE_CACHE_TTL_HOURS должен быть числом, получено: {value!r}"
        ) from exc
    if ttl < 0:
        raise ConfigError("RATE_CACHE_TTL_HOURS не может быть отрицательным")
    return ttl


def normalize_provider(value: str | None) -> str:
    if value is None or not value.strip():
        return PROVIDER_EXCHANGERATE
    key = value.strip().lower()
    provider = _PROVIDER_ALIASES.get(key)
    if provider is None:
        allowed = ", ".join(SUPPORTED_PROVIDERS)
        raise ConfigError(
            f"Неизвестный провайдер курсов: {value!r}. Допустимые значения: {allowed}"
        )
    return provider


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Читает .env (если есть) и переменные окружения.

    Токен Telegram не обязателен на этапе тестов и консольного демо,
    поэтому его проверяет отдельно ``require_telegram_token``.
    """
    env_path = Path(env_file) if env_file else ROOT / ".env"
    load_dotenv(env_path, override=False)

    provider = normalize_provider(os.getenv("CURRENCY_PROVIDER"))
    api_key = (os.getenv("CURRENCY_API_KEY") or "").strip()

    if provider == PROVIDER_EXCHANGERATE and not api_key:
        # Не падаем сразу: демо и open_er_api должны работать без ключа.
        # Понятная ошибка появится в момент запроса курса.
        pass

    database_path = Path(os.getenv("DATABASE_PATH") or "data/wallet.db")
    if not database_path.is_absolute():
        database_path = ROOT / database_path

    return Settings(
        telegram_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
        provider=provider,
        api_key=api_key,
        fallback_enabled=_read_bool(os.getenv("CURRENCY_FALLBACK"), True),
        cache_ttl_hours=_read_ttl(os.getenv("RATE_CACHE_TTL_HOURS")),
        database_path=database_path,
        data_dir=database_path.parent,
        project_root=ROOT,
    )


def require_telegram_token(settings: Settings) -> str:
    token = settings.telegram_token
    if not token or token.startswith("123456:") or "ВАШ_ТОКЕН" in token.upper():
        raise ConfigError(
            "В .env не указан TELEGRAM_BOT_TOKEN. Создайте бота в @BotFather, "
            "скопируйте токен и добавьте строку TELEGRAM_BOT_TOKEN=... в файл .env"
        )
    return token
