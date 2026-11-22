import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from countries import (  # noqa: E402
    find_country,
    get_country,
    popular_dest,
    popular_home,
)


class CountryLookupTests(unittest.TestCase):
    def test_russian_name(self):
        country = find_country("Россия")
        self.assertIsNotNone(country)
        self.assertEqual(country.currency, "RUB")

    def test_alias_rf(self):
        self.assertEqual(find_country("рф").currency, "RUB")

    def test_english_alias(self):
        self.assertEqual(find_country("Italy").currency, "EUR")

    def test_kyrgyz_alias(self):
        self.assertEqual(find_country("Киргизия").currency, "KGS")

    def test_currency_code(self):
        country = find_country("PEN")
        self.assertIsNotNone(country)
        self.assertEqual(country.currency, "PEN")
        self.assertEqual(country.name, "PEN")

    def test_unknown_country(self):
        self.assertIsNone(find_country("Атлантида"))

    def test_get_by_key(self):
        self.assertEqual(get_country("китай").currency, "CNY")

    def test_popular_lists_not_empty(self):
        self.assertGreaterEqual(len(popular_home()), 5)
        self.assertGreaterEqual(len(popular_dest()), 10)

    def test_popular_entries_have_flags(self):
        for country in popular_dest():
            self.assertTrue(country.flag)
            self.assertEqual(len(country.currency), 3)


if __name__ == "__main__":
    unittest.main()
