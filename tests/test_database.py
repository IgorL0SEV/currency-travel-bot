import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database  # noqa: E402


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wallet.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def make_trip(self, user_id=1, rate="11.5", amount="4000"):
        return self.db.create_trip(
            user_id,
            from_country="Россия",
            from_currency="RUB",
            to_country="Китай",
            to_currency="CNY",
            rate=Decimal(rate),
            initial_home=Decimal(amount),
            rate_source="exchangerate_host",
        )


class TripTests(DatabaseTestCase):
    def test_create_trip_balances(self):
        trip = self.make_trip()
        self.assertEqual(trip.balance_home, Decimal("4000.00"))
        self.assertEqual(trip.balance_foreign, Decimal("347.83"))
        self.assertEqual(trip.rate, Decimal("11.500000"))

    def test_trip_becomes_active(self):
        trip = self.make_trip()
        active = self.db.get_active_trip(1)
        self.assertIsNotNone(active)
        self.assertEqual(active.id, trip.id)

    def test_second_trip_becomes_active(self):
        first = self.make_trip()
        second = self.make_trip(rate="12")
        active = self.db.get_active_trip(1)
        self.assertEqual(active.id, second.id)
        self.assertNotEqual(active.id, first.id)

    def test_switch_active_trip(self):
        first = self.make_trip()
        self.make_trip(rate="12")
        switched = self.db.set_active_trip(1, first.id)
        self.assertIsNotNone(switched)
        self.assertEqual(self.db.get_active_trip(1).id, first.id)

    def test_user_isolation(self):
        trip = self.make_trip(user_id=1)
        self.assertIsNone(self.db.get_active_trip(2))
        self.assertEqual(self.db.list_trips(2), [])
        self.assertIsNone(self.db.set_active_trip(2, trip.id))

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            self.db.create_trip(
                1, from_country="A", from_currency="RUB", to_country="B",
                to_currency="CNY", rate=Decimal("0"), initial_home=Decimal("100"),
                rate_source="manual",
            )
        with self.assertRaises(ValueError):
            self.db.create_trip(
                1, from_country="A", from_currency="RUB", to_country="B",
                to_currency="CNY", rate=Decimal("10"), initial_home=Decimal("0"),
                rate_source="manual",
            )

    def test_persistence_after_reopen(self):
        trip = self.make_trip()
        reopened = Database(self.path)
        loaded = reopened.get_trip(trip.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.balance_foreign, trip.balance_foreign)


class ExpenseTests(DatabaseTestCase):
    def test_add_expense_updates_both_balances(self):
        trip = self.make_trip()
        expense, updated = self.db.add_expense(trip.id, Decimal("50"))
        self.assertEqual(expense.amount_home, Decimal("575.00"))
        self.assertEqual(updated.balance_foreign, Decimal("297.83"))
        self.assertEqual(updated.balance_home, Decimal("3425.00"))

    def test_two_expenses_and_totals(self):
        trip = self.make_trip()
        self.db.add_expense(trip.id, Decimal("50"))
        _, updated = self.db.add_expense(trip.id, Decimal("20.5"))
        self.assertEqual(updated.balance_foreign, Decimal("277.33"))
        self.assertEqual(updated.balance_home, Decimal("3189.25"))
        total_foreign, total_home, count = self.db.expenses_total(trip.id)
        self.assertEqual(total_foreign, Decimal("70.50"))
        self.assertEqual(total_home, Decimal("810.75"))
        self.assertEqual(count, 2)

    def test_history_is_newest_first(self):
        trip = self.make_trip()
        self.db.add_expense(trip.id, Decimal("10"))
        self.db.add_expense(trip.id, Decimal("20"))
        history = self.db.list_expenses(trip.id)
        self.assertEqual([item.amount_foreign for item in history], [Decimal("20.00"), Decimal("10.00")])

    def test_negative_balance_allowed(self):
        trip = self.make_trip()
        _, updated = self.db.add_expense(trip.id, Decimal("500"))
        self.assertLess(updated.balance_foreign, 0)
        self.assertLess(updated.balance_home, 0)

    def test_expense_for_missing_trip(self):
        with self.assertRaises(ValueError):
            self.db.add_expense(999, Decimal("10"))

    def test_invalid_expense_amount(self):
        trip = self.make_trip()
        with self.assertRaises(ValueError):
            self.db.add_expense(trip.id, Decimal("0"))

    def test_empty_totals(self):
        trip = self.make_trip()
        self.assertEqual(self.db.expenses_total(trip.id), (Decimal("0.00"), Decimal("0.00"), 0))


class RateChangeTests(DatabaseTestCase):
    def test_rate_change_recalculates_home_balance(self):
        trip = self.make_trip()
        self.db.add_expense(trip.id, Decimal("50"))
        updated = self.db.update_rate(trip.id, Decimal("12"), source="manual", manual=True)
        self.assertEqual(updated.rate, Decimal("12.000000"))
        self.assertEqual(updated.balance_foreign, Decimal("297.83"))
        self.assertEqual(updated.balance_home, Decimal("3573.96"))
        self.assertTrue(updated.rate_manual)

    def test_rate_change_keeps_foreign_balance(self):
        trip = self.make_trip()
        updated = self.db.update_rate(trip.id, Decimal("13"), source="manual", manual=True)
        self.assertEqual(updated.balance_foreign, trip.balance_foreign)


if __name__ == "__main__":
    unittest.main()
