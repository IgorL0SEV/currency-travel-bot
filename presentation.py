"""Тексты интерфейса бота. Без зависимостей от Telegram — удобно тестировать."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from countries import find_country
from database import Expense, Trip
from money import format_money, format_rate

SOURCE_TITLES = {
    "manual": "ручной курс",
    "same": "валюты совпадают",
    "exchangerate_host": "exchangerate.host",
    "open_er_api": "open.er-api.com",
}

LIMIT_EXCHANGERATE = 100


def flag_for(country_name: str) -> str:
    country = find_country(country_name)
    return country.flag if country else "🌐"


def trip_titles(trip: Trip) -> str:
    return (
        f"{flag_for(trip.from_country)} {trip.from_country} → "
        f"{flag_for(trip.to_country)} {trip.to_country}"
    )


def source_label(source: str, manual: bool = False) -> str:
    if manual:
        return SOURCE_TITLES["manual"]
    return SOURCE_TITLES.get(source, source)


def source_title(trip: Trip) -> str:
    return source_label(trip.rate_source, trip.rate_manual)


def rate_line(trip: Trip) -> str:
    return (
        f"1 {trip.to_currency} = {format_rate(trip.rate)} {trip.from_currency} "
        f"({source_title(trip)})"
    )


def balance_line(trip: Trip) -> str:
    return (
        f"{format_money(trip.balance_foreign, trip.to_currency)} = "
        f"{format_money(trip.balance_home, trip.from_currency)}"
    )


def start_text(active_line: str | None = None) -> str:
    lines = [
        "👋 Привет! Я финансовый помощник для путешествий.",
        "",
        "Создаю кошелёк на поездку, беру курс из API и считаю расходы "
        "сразу в двух валютах.",
        "",
        "Как это работает:",
        "1. Создайте путешествие — укажите страну отправления и назначения.",
        "2. Отправьте число — это расход в валюте страны пребывания.",
        "3. Смотрите баланс и историю в любой момент.",
    ]
    if active_line:
        lines.extend(["", active_line])
    lines.extend(["", "Выберите действие:"])
    return "\n".join(lines)


def help_text() -> str:
    return (
        "ℹ️ Что я умею\n\n"
        "/newtrip — создать путешествие\n"
        "/switch — переключить активное путешествие\n"
        "/balance — остаток в двух валютах\n"
        "/history — последние расходы\n"
        "/setrate — изменить курс поездки\n"
        "/status — провайдер курсов и лимиты\n"
        "/cancel — отменить текущий шаг\n\n"
        "Любое сообщение с числом — расход в валюте страны пребывания. "
        "Например, если активна поездка в Китай, сообщение «100» значит 100 CNY."
    )


def balance_text(trip: Trip, spent_foreign: Decimal | None = None, spent_home: Decimal | None = None) -> str:
    lines = [
        f"💰 Баланс — {trip.to_country}",
        trip_titles(trip),
        "",
        f"Остаток: {balance_line(trip)}",
        f"Курс поездки: {rate_line(trip)}",
        f"Начальная сумма: {format_money(trip.initial_home, trip.from_currency)}",
    ]
    if spent_foreign is not None and spent_home is not None:
        lines.append(
            f"Израсходовано: {format_money(spent_foreign, trip.to_currency)} = "
            f"{format_money(spent_home, trip.from_currency)}"
        )
    if trip.balance_foreign < 0:
        lines.extend(["", "⚠️ Баланс ушёл в минус — расходы превысили начальную сумму."])
    return "\n".join(lines)


def history_text(trip: Trip, expenses: list[Expense], total_foreign: Decimal, total_home: Decimal, total_count: int) -> str:
    lines = [f"📜 История расходов — {trip.to_country}", ""]
    if not expenses:
        lines.append("Расходов пока нет. Отправьте число — я предложу учесть его как расход.")
        return "\n".join(lines)

    lines.append(f"Показаны последние {len(expenses)} из {total_count}.")
    for expense in expenses:
        moment = _format_moment(expense.created_at)
        lines.append(
            f"• {moment} — {format_money(expense.amount_foreign, trip.to_currency)} = "
            f"{format_money(expense.amount_home, trip.from_currency)}"
        )
    lines.extend(
        [
            "",
            f"Итого: {format_money(total_foreign, trip.to_currency)} = "
            f"{format_money(total_home, trip.from_currency)}",
        ]
    )
    return "\n".join(lines)


def trips_text(trips: list[Trip], active_id: int | None) -> str:
    if not trips:
        return "🧳 У вас пока нет путешествий. Создайте первое — это займёт меньше минуты."
    lines = ["🧳 Ваши путешествия", ""]
    for trip in trips:
        mark = "✅ " if trip.id == active_id else "• "
        lines.append(f"{mark}{trip_titles(trip)} — {balance_line(trip)}")
    lines.extend(["", "Нажмите на поездку, чтобы сделать её активной."])
    return "\n".join(lines)


def status_text(api_status: dict, db_counts: dict, active: Trip | None) -> str:
    usage = api_status.get("usage") or {}
    provider = api_status.get("provider", "")
    lines = [
        "⚙️ Статус",
        "",
        f"Провайдер курсов: {api_status.get('provider_title', provider)}",
        f"Ключ API: {'задан' if api_status.get('api_key_present') else 'не задан'}",
        f"Резервный источник: {'включён' if api_status.get('fallback_enabled') else 'выключен'}",
        f"Кэш курсов: {api_status.get('cache_entries', 0)} записей, "
        f"TTL {api_status.get('cache_ttl_hours', 0):g} ч",
        "",
        f"Запросов к API в этом месяце: {api_status.get('usage_total', 0)}",
    ]
    for key, value in usage.items():
        title = SOURCE_TITLES.get(key, key)
        lines.append(f"• {title}: {value}")
    if provider == "exchangerate_host":
        used = int(usage.get("exchangerate_host", 0))
        lines.append(f"Бесплатный лимит exchangerate.host: {used} из {LIMIT_EXCHANGERATE}")
        if used >= LIMIT_EXCHANGERATE:
            lines.append("⚠️ Лимит исчерпан — до следующего месяца поможет резервный источник.")
        elif used >= LIMIT_EXCHANGERATE * 0.8:
            lines.append("⚠️ Израсходовано больше 80% лимита. Кэш и ручной курс экономят запросы.")
    lines.extend(
        [
            "",
            f"В базе: {db_counts.get('trips', 0)} поездок, {db_counts.get('expenses', 0)} расходов.",
        ]
    )
    if active:
        lines.append(f"Активная поездка: {trip_titles(active)}.")
    return "\n".join(lines)


def expense_offer_text(trip: Trip, amount_foreign: Decimal, amount_home: Decimal) -> str:
    return (
        f"💸 {format_money(amount_foreign, trip.to_currency)} = "
        f"{format_money(amount_home, trip.from_currency)}\n\n"
        f"Учесть как расход в поездке «{trip.to_country}»?"
    )


def expense_done_text(trip: Trip, amount_foreign: Decimal, amount_home: Decimal) -> str:
    lines = [
        f"✅ Расход учтён: {format_money(amount_foreign, trip.to_currency)} = "
        f"{format_money(amount_home, trip.from_currency)}",
        f"Остаток: {balance_line(trip)}",
    ]
    if trip.balance_foreign < 0:
        lines.append("⚠️ Баланс ушёл в минус.")
    return "\n".join(lines)


def _format_moment(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return moment.strftime("%d.%m %H:%M")
