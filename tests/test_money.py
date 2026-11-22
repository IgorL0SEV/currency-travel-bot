import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money import (  # noqa: E402
    foreign_from_home,
    format_money,
    format_rate,
    home_from_foreign,
    money,
    parse_amount,
    parse_rate,
    rate_value,
)


class ParseAmountTests(unittest.TestCase):
    def test_plain_integer(self):
        self.assertEqual(parse_amount("1000"), Decimal("1000"))

    def test_spaces_and_comma(self):
        self.assertEqual(parse_amount("1 000,50"), Decimal("1000.50"))

    def test_dot_decimal(self):
        self.assertEqual(parse_amount("1000.5"), Decimal("1000.5"))

    def test_thousands_and_decimal(self):
        self.assertEqual(parse_amount("1,000.50"), Decimal("1000.50"))

    def test_currency_symbols_are_ignored(self):
        self.assertEqual(parse_amount("100 €"), Decimal("100"))

    def test_rejects_zero(self):
        self.assertIsNone(parse_amount("0"))

    def test_rejects_negative(self):
        self.assertIsNone(parse_amount("-5"))

    def test_rejects_text(self):
        self.assertIsNone(parse_amount("много"))

    def test_rejects_two_dots(self):
        self.assertIsNone(parse_amount("12.3.4"))

    def test_rejects_empty(self):
        self.assertIsNone(parse_amount("   "))

    def test_rate_uses_same_rules(self):
        self.assertEqual(parse_rate("104,5"), Decimal("104.5"))


class RoundingTests(unittest.TestCase):
    def test_money_half_up(self):
        self.assertEqual(money("2.675"), Decimal("2.68"))

    def test_money_rounds_down(self):
        self.assertEqual(money("2.674"), Decimal("2.67"))

    def test_rate_quantized_to_six_places(self):
        self.assertEqual(rate_value("0.009612345"), Decimal("0.009612"))

    def test_foreign_from_home(self):
        self.assertEqual(foreign_from_home(Decimal("4000"), Decimal("11.5")), Decimal("347.83"))

    def test_home_from_foreign(self):
        self.assertEqual(home_from_foreign(Decimal("50"), Decimal("11.5")), Decimal("575.00"))

    def test_zero_rate_rejected(self):
        with self.assertRaises(ValueError):
            foreign_from_home(Decimal("100"), Decimal("0"))


class FormattingTests(unittest.TestCase):
    def test_money_russian_style(self):
        self.assertEqual(format_money("1234567.5", "RUB"), "1 234 567,50 RUB")

    def test_money_without_currency(self):
        self.assertEqual(format_money("10"), "10,00")

    def test_rate_regular(self):
        self.assertEqual(format_rate("104.25"), "104,2500")

    def test_rate_small(self):
        self.assertEqual(format_rate("0.0031"), "0,003100")


if __name__ == "__main__":
    unittest.main()
