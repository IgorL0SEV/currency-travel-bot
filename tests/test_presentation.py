import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Expense, Trip  # noqa: E402
from presentation import (  # noqa: E402
    balance_text,
    expense_done_text,
    expense_offer_text,
    history_text,
    rate_line,
    start_text,
    status_text,
    trip_titles,
    trips_text,
)


def make_trip(**overrides):
    values = dict(
        id=1,
        user_id=1,
        from_country="Россия",
        from_currency="RUB",
        to_country="Китай",
        to_currency="CNY",
        rate=Decimal("11.5"),
        rate_source="exchangerate_host",
        rate_manual=False,
        initial_home=Decimal("4000"),
        balance_foreign=Decimal("297.83"),
        balance_home=Decimal("3425.00"),
        created_at="2026-11-22T12:00:00+03:00",
    )
    values.update(overrides)
    return Trip(**values)


def make_expense(**overrides):
    values = dict(
        id=1,
        trip_id=1,
        amount_foreign=Decimal("50"),
        amount_home=Decimal("575"),
        rate=Decimal("11.5"),
        created_at="2026-11-22T14:05:00+03:00",
    )
    values.update(overrides)
    return Expense(**values)


class TripTextTests(unittest.TestCase):
    def test_trip_titles_with_flags(self):
        text = trip_titles(make_trip())
        self.assertIn("🇷🇺 Россия", text)
        self.assertIn("🇨🇳 Китай", text)

    def test_rate_line(self):
        self.assertEqual(
            rate_line(make_trip()),
            "1 CNY = 11,5000 RUB (exchangerate.host)",
        )

    def test_manual_rate_line(self):
        trip = make_trip(rate_source="manual", rate_manual=True)
        self.assertIn("ручной курс", rate_line(trip))

    def test_balance_text(self):
        text = balance_text(make_trip())
        self.assertIn("Остаток: 297,83 CNY = 3 425,00 RUB", text)
        self.assertIn("Начальная сумма: 4 000,00 RUB", text)

    def test_negative_balance_warning(self):
        text = balance_text(make_trip(balance_foreign=Decimal("-10"), balance_home=Decimal("-115")))
        self.assertIn("в минус", text)


class HistoryTextTests(unittest.TestCase):
    def test_empty_history(self):
        text = history_text(make_trip(), [], Decimal("0"), Decimal("0"), 0)
        self.assertIn("Расходов пока нет", text)

    def test_history_entries_and_total(self):
        text = history_text(
            make_trip(),
            [make_expense()],
            Decimal("50"),
            Decimal("575"),
            1,
        )
        self.assertIn("22.11 14:05", text)
        self.assertIn("50,00 CNY = 575,00 RUB", text)
        self.assertIn("Итого: 50,00 CNY = 575,00 RUB", text)


class TripsTextTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertIn("пока нет путешествий", trips_text([], None))

    def test_active_marker(self):
        text = trips_text([make_trip(), make_trip(id=2, to_country="Турция", to_currency="TRY")], 1)
        self.assertIn("✅", text)
        self.assertIn("Турция", text)


class StatusTextTests(unittest.TestCase):
    def base_status(self, used):
        return {
            "provider": "exchangerate_host",
            "provider_title": "exchangerate.host",
            "api_key_present": True,
            "fallback_enabled": True,
            "cache_entries": 3,
            "cache_ttl_hours": 12,
            "usage": {"exchangerate_host": used},
            "usage_total": used,
        }

    def test_regular_status(self):
        text = status_text(self.base_status(5), {"trips": 1, "expenses": 2}, make_trip())
        self.assertIn("exchangerate.host: 5", text)
        self.assertIn("В базе: 1 поездок, 2 расходов", text)
        self.assertIn("Активная поездка", text)

    def test_warning_at_eighty_percent(self):
        text = status_text(self.base_status(85), {"trips": 0, "expenses": 0}, None)
        self.assertIn("больше 80%", text)

    def test_warning_when_exhausted(self):
        text = status_text(self.base_status(100), {"trips": 0, "expenses": 0}, None)
        self.assertIn("Лимит исчерпан", text)


class MiscTextTests(unittest.TestCase):
    def test_start_with_active_line(self):
        text = start_text("Активная поездка: Россия → Китай.")
        self.assertIn("Активная поездка", text)
        self.assertTrue(text.endswith("Выберите действие:"))

    def test_expense_offer(self):
        text = expense_offer_text(make_trip(), Decimal("50"), Decimal("575"))
        self.assertIn("50,00 CNY = 575,00 RUB", text)
        self.assertIn("Учесть как расход", text)

    def test_expense_done(self):
        text = expense_done_text(make_trip(), Decimal("50"), Decimal("575"))
        self.assertIn("Расход учтён", text)
        self.assertIn("Остаток:", text)


if __name__ == "__main__":
    unittest.main()
