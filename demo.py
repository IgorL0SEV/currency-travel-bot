"""Живая проверка проекта без Telegram.

Скрипт получает настоящий курс, создаёт поездку во временной базе,
добавляет два расхода и печатает баланс и историю — то же, что
показывает бот.

    python demo.py
    python demo.py --from USD --to EUR --amount 1000
"""

from __future__ import annotations

import argparse
import tempfile
from decimal import Decimal
from pathlib import Path

from config import load_settings
from current_api import CurrencyAPI, CurrencyAPIError
from database import Database
from money import format_money, format_rate, home_from_foreign
from presentation import balance_text, history_text, rate_line, trip_titles

COUNTRY_BY_CURRENCY = {
    "RUB": "Россия",
    "CNY": "Китай",
    "EUR": "Италия",
    "USD": "США",
    "TRY": "Турция",
    "KZT": "Казахстан",
    "GEL": "Грузия",
    "THB": "Таиланд",
    "AED": "ОАЭ",
    "EGP": "Египет",
}


def _country_name(currency: str) -> str:
    return COUNTRY_BY_CURRENCY.get(currency.upper(), currency.upper())


def main() -> int:
    parser = argparse.ArgumentParser(description="Демонстрация кошелька путешественника")
    parser.add_argument("--from", dest="from_currency", default="RUB", help="домашняя валюта")
    parser.add_argument("--to", dest="to_currency", default="CNY", help="валюта поездки")
    parser.add_argument("--amount", default="4000", help="начальная сумма в домашней валюте")
    parser.add_argument("--keep", action="store_true", help="сохранить демо-базу в data/demo.db")
    args = parser.parse_args()

    try:
        amount = Decimal(str(args.amount).replace(",", "."))
    except Exception:
        print("Сумма должна быть числом, например 4000 или 1500,50")
        return 2
    if amount <= 0:
        print("Сумма должна быть положительной.")
        return 2

    home_currency = args.from_currency.upper()
    trip_currency = args.to_currency.upper()
    settings = load_settings()
    api = CurrencyAPI(settings)

    print("Финансовый помощник — консольная проверка")
    print(f"Провайдер курсов: {settings.provider_title}")
    print(f"Домашняя валюта: {home_currency}, валюта поездки: {trip_currency}")
    print()

    print("1. Получаю курс…")
    try:
        rate_result = api.get_rate(trip_currency, home_currency)
    except CurrencyAPIError as exc:
        print(f"   ✗ Не удалось получить курс: {exc}")
        print("   Проверьте .env: CURRENCY_PROVIDER, CURRENCY_API_KEY, доступ в интернет.")
        return 1

    suffix = " (резервный источник)" if rate_result.fallback else ""
    print(
        f"   ✓ 1 {trip_currency} = {format_rate(rate_result.rate)} {home_currency} "
        f"[{rate_result.provider_title}{suffix}]"
    )
    print()

    temporary = None
    if args.keep:
        database_path = settings.data_dir / "demo.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory()
        database_path = Path(temporary.name) / "demo.db"

    try:
        database = Database(database_path)
        user_id = 1
        trip = database.create_trip(
            user_id,
            from_country=_country_name(home_currency),
            from_currency=home_currency,
            to_country=_country_name(trip_currency),
            to_currency=trip_currency,
            rate=rate_result.rate,
            initial_home=amount,
            rate_source=rate_result.provider,
        )

        print("2. Поездка создана")
        print(f"   {trip_titles(trip)}")
        print(f"   Начальный баланс: {format_money(trip.balance_foreign, trip.to_currency)} = "
              f"{format_money(trip.balance_home, trip.from_currency)}")
        print(f"   Курс: {rate_line(trip)}")
        print()

        print("3. Учитываю расходы")
        for spent in (Decimal("50"), Decimal("20.5")):
            if spent >= trip.balance_foreign:
                continue
            expense, trip = database.add_expense(trip.id, spent)
            print(
                f"   • {format_money(expense.amount_foreign, trip.to_currency)} = "
                f"{format_money(expense.amount_home, trip.from_currency)}"
            )
        print()

        spent_foreign, spent_home, count = database.expenses_total(trip.id)
        expenses = database.list_expenses(trip.id, limit=20)
        print("4. Баланс")
        print(balance_text(trip, spent_foreign, spent_home))
        print()
        print("5. История")
        print(history_text(trip, expenses, spent_foreign, spent_home, count))
        print()

        check = home_from_foreign(Decimal("10"), trip.rate)
        print(
            f"Контроль: 10 {trip.to_currency} = "
            f"{format_money(check, trip.from_currency)} по курсу поездки."
        )
        if args.keep:
            print(f"Демо-база сохранена: {database_path}")
        print("Проверка завершена успешно.")
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
