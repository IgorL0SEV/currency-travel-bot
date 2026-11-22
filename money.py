"""Денежные расчёты: Decimal, единое округление, разбор и форматирование сумм.

Все вычисления с деньгами и курсами проходят только через этот модуль,
чтобы не было расхождений «где-то умножили, где-то поделили».
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY_STEP = Decimal("0.01")
RATE_STEP = Decimal("0.000001")

_SPACES = re.compile(r"[\s  ]+")
_NUMBER = re.compile(r"^-?\d+([.,]\d+)?$")


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Аккуратно приводит значение к Decimal (float — через str, без мусора)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("Логическое значение нельзя использовать как сумму")
    return Decimal(str(value))


def money(value: Decimal | int | float | str) -> Decimal:
    """Округляет денежную сумму до копеек (half up)."""
    return to_decimal(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def rate_value(value: Decimal | int | float | str) -> Decimal:
    """Округляет курс до шести знаков — достаточно для бытовых валют."""
    return to_decimal(value).quantize(RATE_STEP, rounding=ROUND_HALF_UP)


def _clean_number(text: str) -> str:
    cleaned = _SPACES.sub("", str(text).strip())
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    return cleaned.replace("₽", "").replace("$", "").replace("€", "").strip()


def parse_amount(text: str) -> Decimal | None:
    """Разбирает сумму из сообщения пользователя.

    Понимает «1000», «1 000,50», «1000.5», «1,000.50».
    Возвращает None, если это не число, ноль или отрицательное значение.
    """
    if text is None:
        return None
    cleaned = _clean_number(text)
    if not cleaned:
        return None

    # Если есть и запятая, и точка — десятичный разделитель тот, что правее.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    if not _NUMBER.match(cleaned):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount


def parse_rate(text: str) -> Decimal | None:
    """Курс — положительное число; правила разбора те же, что у суммы."""
    return parse_amount(text)


def foreign_from_home(home_amount: Decimal, home_per_foreign: Decimal) -> Decimal:
    """Сколько валюты поездки получится за домашнюю сумму."""
    if home_per_foreign <= 0:
        raise ValueError("Курс должен быть положительным")
    return money(home_amount / home_per_foreign)


def home_from_foreign(foreign_amount: Decimal, home_per_foreign: Decimal) -> Decimal:
    """Сколько домашней валюты стоит сумма в валюте поездки."""
    return money(foreign_amount * home_per_foreign)


def format_money(value: Decimal | int | float | str, currency: str | None = None) -> str:
    """Форматирует сумму по-русски: «1 234,56 EUR»."""
    amount = money(value)
    text = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} {currency}" if currency else text


def format_rate(value: Decimal | int | float | str) -> str:
    """Курс для интерфейса: «104,2500», а для очень малых — «0,003100»."""
    rate = rate_value(value)
    digits = 6 if abs(rate) < Decimal("0.01") else 4
    text = f"{rate:.{digits}f}".replace(".", ",")
    return text
