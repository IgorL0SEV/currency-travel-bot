"""Сценарии бота без Telegram и без сети.

Подменяем TeleBot и API курсов: проверяем состояния диалога,
кнопки, создание поездки, расход и смену курса.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot as bot_module  # noqa: E402
from config import Settings  # noqa: E402
from current_api import CurrencyAPIError, RateResult  # noqa: E402
from database import Database  # noqa: E402


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self, text, chat_id=1, user_id=1, message_id=1):
        self.text = text
        self.chat = FakeChat(chat_id)
        self.from_user = FakeUser(user_id)
        self.message_id = message_id


class FakeCall:
    def __init__(self, data, message_id, chat_id=1, user_id=1):
        self.id = f"cb-{data}"
        self.data = data
        self.message = FakeMessage("", chat_id, user_id, message_id)
        self.from_user = FakeUser(user_id)


class FakeBot:
    """Минимальная замена telebot.TeleBot для проверки логики."""

    def __init__(self, token, parse_mode=None):
        self.message_handlers = []
        self.callback_handlers = []
        self.sent = []
        self.edits = []
        self.answers = []
        self._next_id = 1

    def message_handler(self, **kwargs):
        def decorator(function):
            self.message_handlers.append((kwargs, function))
            return function

        return decorator

    def callback_query_handler(self, **kwargs):
        def decorator(function):
            self.callback_handlers.append((kwargs, function))
            return function

        return decorator

    def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        message = FakeMessage(text, chat_id, 1, self._next_id)
        self._next_id += 1
        self.sent.append(message)
        return message

    def edit_message_text(self, text, chat_id, message_id, reply_markup=None, **kwargs):
        self.edits.append((message_id, text))
        return FakeMessage(text, chat_id, 1, message_id)

    def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        self.answers.append(text)

    # --- помощники для теста ------------------------------------------------

    def last_text(self):
        return self.sent[-1].text if self.sent else ""

    def last_edit(self):
        return self.edits[-1][1] if self.edits else ""

    def last_id(self):
        return self.sent[-1].message_id if self.sent else 0

    def command(self, name):
        for kwargs, function in self.message_handlers:
            if name in (kwargs.get("commands") or []):
                return function
        raise KeyError(f"Нет обработчика команды {name}")

    def text_handler(self):
        for kwargs, function in self.message_handlers:
            if kwargs.get("content_types") == ["text"]:
                return function
        raise KeyError("Нет обработчика текста")

    def callback(self, data, message_id):
        self.callback_handlers[0][1](FakeCall(data, message_id))


class StubAPI:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get_rate(self, base, quote):
        self.calls.append((base, quote))
        if self.error:
            raise self.error
        return RateResult(
            base=base,
            quote=quote,
            rate=Decimal("12.2549"),
            provider="open_er_api",
            fetched_at=datetime.now(timezone.utc),
        )

    def status(self):
        return {
            "provider": "open_er_api",
            "provider_title": "open.er-api.com",
            "api_key_present": False,
            "fallback_enabled": True,
            "cache_entries": 0,
            "cache_ttl_hours": 12,
            "usage": {},
            "usage_total": 0,
        }


class BotFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            telegram_token="",
            provider="open_er_api",
            api_key="",
            fallback_enabled=True,
            cache_ttl_hours=12,
            database_path=Path(self.tmp.name) / "wallet.db",
            data_dir=Path(self.tmp.name),
            project_root=Path(self.tmp.name),
        )
        self.db = Database(self.settings.database_path)
        self.api = StubAPI()
        self._original_telebot = bot_module.telebot.TeleBot
        bot_module.telebot.TeleBot = FakeBot
        self.bot = bot_module.build_bot("token", self.db, self.api, self.settings)

    def tearDown(self):
        bot_module.telebot.TeleBot = self._original_telebot
        self.tmp.cleanup()

    # --- шаги ---------------------------------------------------------------

    def send_command(self, command):
        self.bot.command(command)(FakeMessage(f"/{command}"))

    def send_text(self, text):
        self.bot.text_handler()(FakeMessage(text))

    def click(self, data, message_id):
        self.bot.callback(data, message_id)

    def create_trip(self, amount="4000", rate_step="new:rate_ok"):
        self.send_command("newtrip")
        panel = self.bot.last_id()
        self.click("new:from:россия", panel)
        self.click("new:to:китай", panel)
        self.click(rate_step, panel)
        self.send_text(amount)
        return panel

    def assert_no_internal_error(self):
        self.assertNotIn("Внутренняя ошибка", self.bot.last_text())

    # --- тесты --------------------------------------------------------------

    def test_start_shows_menu(self):
        self.send_command("start")
        self.assertIn("Привет", self.bot.last_text())
        self.assert_no_internal_error()

    def test_full_trip_expense_balance_history(self):
        panel = self.create_trip()
        self.assertIn("Поездка создана", self.bot.last_text())
        self.assertIn("326,40 CNY = 4 000,00 RUB", self.bot.last_text())

        self.send_text("50")
        offer_id = self.bot.last_id()
        self.assertIn("Учесть как расход", self.bot.last_text())
        self.click("exp:yes", offer_id)
        self.assertIn("Расход учтён", self.bot.last_edit())
        self.assertIn("3 387,25 RUB", self.bot.last_edit())

        self.send_command("balance")
        self.assertIn("Остаток: 276,40 CNY = 3 387,25 RUB", self.bot.last_text())

        self.send_command("history")
        self.assertIn("История расходов", self.bot.last_text())
        self.assertIn("50,00 CNY = 612,75 RUB", self.bot.last_text())

    def test_expense_can_be_declined(self):
        self.create_trip()
        self.send_text("50")
        offer_id = self.bot.last_id()
        self.click("exp:no", offer_id)
        self.assertIn("не записан", self.bot.last_edit())
        self.assertEqual(self.db.expenses_total(1)[2], 0)

    def test_number_without_trip_is_explained(self):
        self.send_text("100")
        self.assertIn("Сначала создайте путешествие", self.bot.last_text())
        self.assertEqual(self.db.list_trips(1), [])

    def test_manual_rate_flow(self):
        self.send_command("newtrip")
        panel = self.bot.last_id()
        self.click("new:from:россия", panel)
        self.click("new:to:китай", panel)
        self.click("new:rate_manual", panel)
        self.assertIn("Введите курс вручную", self.bot.last_edit())
        self.send_text("13,5")
        self.send_text("1000")
        trip = self.db.get_active_trip(1)
        self.assertIsNotNone(trip)
        self.assertEqual(trip.rate, Decimal("13.500000"))
        self.assertTrue(trip.rate_manual)

    def test_api_failure_offers_manual_rate(self):
        self.api.error = CurrencyAPIError("Сервис недоступен")
        self.send_command("newtrip")
        panel = self.bot.last_id()
        self.click("new:from:россия", panel)
        self.click("new:to:китай", panel)
        self.assertIn("Не удалось получить курс", self.bot.last_edit())
        self.click("new:rate_manual", panel)
        self.send_text("12")
        self.send_text("1200")
        trip = self.db.get_active_trip(1)
        self.assertIsNotNone(trip)
        self.assertEqual(trip.rate_source, "manual")

    def test_unknown_country_is_reported(self):
        self.send_command("newtrip")
        self.send_text("Атлантида")
        self.assertIn("Не нашёл такую страну", self.bot.last_text())
        self.assert_no_internal_error()

    def test_unknown_text_is_not_expense(self):
        self.create_trip()
        self.send_text("привет")
        self.assertIn("Не понял", self.bot.last_text())
        self.assertEqual(self.db.expenses_total(1)[2], 0)

    def test_set_rate_recalculates_balance(self):
        self.create_trip()
        self.send_command("setrate")
        self.send_text("13")
        self.assertIn("Курс обновлён", self.bot.last_text())
        self.assertIn("13,0000", self.bot.last_text())
        trip = self.db.get_active_trip(1)
        self.assertEqual(trip.rate, Decimal("13.000000"))

    def test_status_lists_provider_and_limit(self):
        self.send_command("status")
        text = self.bot.last_text()
        self.assertIn("Провайдер курсов", text)
        self.assertIn("open.er-api.com", text)

    def test_trips_list_and_switch(self):
        first = self.db.create_trip(
            1,
            from_country="Россия",
            from_currency="RUB",
            to_country="Турция",
            to_currency="TRY",
            rate=Decimal("2.5"),
            initial_home=Decimal("5000"),
            rate_source="manual",
        )
        self.send_command("switch")
        self.assertIn("Ваши путешествия", self.bot.last_text())
        panel = self.bot.last_id()
        self.click(f"trip:switch:{first.id}", panel)
        self.assertEqual(self.db.get_active_trip(1).id, first.id)
        self.assertIn("Баланс", self.bot.last_edit())

    def test_cancel_clears_flow(self):
        self.send_command("newtrip")
        self.send_command("cancel")
        self.assertIn("отменён", self.bot.last_text())
        self.send_text("Италия")
        self.assertIn("Не понял", self.bot.last_text())


if __name__ == "__main__":
    unittest.main()
