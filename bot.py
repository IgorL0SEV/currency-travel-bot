"""Telegram-бот «мини-кошелёк путешественника».

Запуск: ``python bot.py`` (токен и провайдер курсов — в .env).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from config import (
    PROVIDER_EXCHANGERATE,
    ConfigError,
    Settings,
    load_settings,
    require_telegram_token,
)
from countries import Country, find_country, get_country, popular_dest, popular_home
from current_api import CurrencyAPI, CurrencyAPIError
from database import Database, Trip
from money import (
    format_money,
    format_rate,
    home_from_foreign,
    parse_amount,
    parse_rate,
)
from presentation import (
    balance_text,
    expense_done_text,
    expense_offer_text,
    help_text,
    history_text,
    rate_line,
    source_label,
    start_text,
    status_text,
    trip_titles,
    trips_text,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Состояния диалога (в памяти процесса)
# ---------------------------------------------------------------------------


class ConversationStates:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[int, dict] = {}

    def set(self, chat_id: int, step: str, **data) -> None:
        with self._lock:
            payload = {"step": step}
            payload.update({key: value for key, value in data.items() if key != "step"})
            self._data[int(chat_id)] = payload

    def get(self, chat_id: int) -> dict | None:
        with self._lock:
            value = self._data.get(int(chat_id))
            return dict(value) if value else None

    def clear(self, chat_id: int) -> None:
        with self._lock:
            self._data.pop(int(chat_id), None)


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text, callback_data=data)


def main_menu() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        _button("✈️ Создать путешествие", "menu:new"),
        _button("🧳 Мои путешествия", "menu:trips"),
    )
    keyboard.add(
        _button("💰 Баланс", "menu:balance"),
        _button("📜 История расходов", "menu:history"),
    )
    keyboard.add(
        _button("🔁 Изменить курс", "menu:rate"),
        _button("⚙️ Статус и лимиты", "menu:status"),
    )
    keyboard.add(_button("ℹ️ Помощь", "menu:help"))
    return keyboard


def cancel_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(_button("❌ Отмена", "new:cancel"))
    return keyboard


def country_keyboard(prefix: str, countries: list[Country]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        *[_button(country.button, f"new:{prefix}:{country.key}") for country in countries]
    )
    keyboard.add(_button("🌍 Другая страна / код валюты", f"new:{prefix}:other"))
    keyboard.add(_button("❌ Отмена", "new:cancel"))
    return keyboard


def rate_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        _button("✅ Да, подходит", "new:rate_ok"),
        _button("✏️ Ввести вручную", "new:rate_manual"),
    )
    keyboard.add(_button("❌ Отмена", "new:cancel"))
    return keyboard


def manual_only_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        _button("✏️ Ввести курс вручную", "new:rate_manual"),
        _button("❌ Отмена", "new:cancel"),
    )
    return keyboard


def expense_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        _button("✅ Да, учесть", "exp:yes"),
        _button("❌ Нет", "exp:no"),
    )
    return keyboard


def trips_keyboard(trips: list[Trip], active_id: int | None) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for trip in trips[:12]:
        mark = "✅ " if trip.id == active_id else ""
        label = (
            f"{mark}{trip.to_country} — "
            f"{format_money(trip.balance_foreign, trip.to_currency)}"
        )
        keyboard.add(_button(label, f"trip:switch:{trip.id}"))
    keyboard.add(_button("➕ Новое путешествие", "menu:new"))
    keyboard.add(_button("⬅️ В меню", "menu:home"))
    return keyboard


# ---------------------------------------------------------------------------
# Сборка бота
# ---------------------------------------------------------------------------


def build_bot(token: str, db: Database, api: CurrencyAPI, settings: Settings) -> telebot.TeleBot:
    bot = telebot.TeleBot(token, parse_mode=None)
    states = ConversationStates()

    # --- служебные помощники ------------------------------------------------

    def render(chat_id: int, message_id: int | None, text: str, keyboard=None) -> int:
        """Правит сообщение панели, а если не получилось — отправляет новое."""
        if message_id:
            try:
                bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=keyboard,
                )
                return int(message_id)
            except ApiTelegramException as exc:
                if "message is not modified" in str(exc):
                    return int(message_id)
                log.debug("Не удалось изменить сообщение: %s", exc)
            except Exception as exc:  # noqa: BLE001 — сеть тоже может подвести
                log.warning("Не удалось изменить сообщение: %s", exc)
        sent = bot.send_message(chat_id, text, reply_markup=keyboard)
        return sent.message_id

    def answer(call, text: str | None = None) -> None:
        try:
            bot.answer_callback_query(call.id, text=text)
        except ApiTelegramException:
            pass

    def no_active_trip(chat_id: int, message_id: int | None) -> int:
        return render(
            chat_id,
            message_id,
            "Сначала создайте путешествие — в нём будут храниться баланс и расходы.",
            main_menu(),
        )

    def user_of(message) -> tuple[int, int]:
        return message.chat.id, message.from_user.id

    # --- меню и команды -----------------------------------------------------

    def send_home(chat_id: int, message_id: int | None = None, user_id: int | None = None) -> int:
        active = db.get_active_trip(user_id) if user_id is not None else None
        active_line = f"Активная поездка: {trip_titles(active)}." if active else None
        return render(chat_id, message_id, start_text(active_line), main_menu())

    @bot.message_handler(commands=["start"])
    def cmd_start(message) -> None:
        chat_id, user_id = user_of(message)
        states.clear(chat_id)
        db.get_or_create_user(user_id)
        send_home(chat_id, None, user_id)

    @bot.message_handler(commands=["help"])
    def cmd_help(message) -> None:
        chat_id, _ = user_of(message)
        bot.send_message(chat_id, help_text(), reply_markup=main_menu())

    @bot.message_handler(commands=["newtrip"])
    def cmd_newtrip(message) -> None:
        chat_id, user_id = user_of(message)
        begin_new_trip(chat_id, user_id, None)

    @bot.message_handler(commands=["switch", "trips"])
    def cmd_trips(message) -> None:
        chat_id, user_id = user_of(message)
        show_trips(chat_id, None, user_id)

    @bot.message_handler(commands=["balance"])
    def cmd_balance(message) -> None:
        chat_id, user_id = user_of(message)
        show_balance(chat_id, None, user_id)

    @bot.message_handler(commands=["history"])
    def cmd_history(message) -> None:
        chat_id, user_id = user_of(message)
        show_history(chat_id, None, user_id)

    @bot.message_handler(commands=["setrate"])
    def cmd_setrate(message) -> None:
        chat_id, user_id = user_of(message)
        begin_set_rate(chat_id, user_id, None)

    @bot.message_handler(commands=["status"])
    def cmd_status(message) -> None:
        chat_id, user_id = user_of(message)
        show_status(chat_id, None, user_id)

    @bot.message_handler(commands=["cancel"])
    def cmd_cancel(message) -> None:
        chat_id, _ = user_of(message)
        states.clear(chat_id)
        bot.send_message(chat_id, "Текущий шаг отменён.", reply_markup=main_menu())

    # --- создание путешествия ----------------------------------------------

    def begin_new_trip(chat_id: int, user_id: int, message_id: int | None) -> None:
        states.set(chat_id, "from_country", user_id=user_id, panel_id=message_id)
        render(
            chat_id,
            message_id,
            "✈️ Новое путешествие\n\n"
            "Откуда выезжаете? Валюту определю по стране.",
            country_keyboard("from", popular_home()),
        )

    def handle_from(chat_id: int, user_id: int, message_id: int | None, country: Country) -> None:
        states.set(
            chat_id,
            "to_country",
            user_id=user_id,
            panel_id=message_id,
            from_country=country.name,
            from_currency=country.currency,
        )
        render(
            chat_id,
            message_id,
            f"Откуда: {country.button} ({country.currency})\n\nКуда едете?",
            country_keyboard("to", popular_dest()),
        )

    def handle_to(chat_id: int, user_id: int, message_id: int | None, country: Country) -> None:
        state = states.get(chat_id) or {}
        if not state.get("from_currency"):
            begin_new_trip(chat_id, user_id, message_id)
            return
        states.set(
            chat_id,
            "rate",
            user_id=user_id,
            panel_id=message_id,
            from_country=state["from_country"],
            from_currency=state["from_currency"],
            to_country=country.name,
            to_currency=country.currency,
        )
        fetch_rate(chat_id, user_id, message_id)

    def fetch_rate(chat_id: int, user_id: int, message_id: int | None) -> None:
        state = states.get(chat_id) or {}
        panel = render(chat_id, message_id, "⏳ Получаю курс…")

        from_currency = state["from_currency"]
        to_currency = state["to_currency"]

        if from_currency == to_currency:
            offer_rate(
                chat_id,
                user_id,
                panel,
                Decimal("1"),
                "same",
                False,
                datetime.now(timezone.utc),
            )
            return

        try:
            result = api.get_rate(to_currency, from_currency)
        except CurrencyAPIError as exc:
            log.warning("Курс %s/%s не получен: %s", to_currency, from_currency, exc)
            states.set(
                chat_id,
                "rate_error",
                user_id=user_id,
                panel_id=panel,
                from_country=state["from_country"],
                from_currency=from_currency,
                to_country=state["to_country"],
                to_currency=to_currency,
            )
            render(
                chat_id,
                panel,
                "⚠️ Не удалось получить курс.\n\n"
                f"{exc}\n\n"
                "Можно ввести курс вручную — например, по курсу обменника.",
                manual_only_keyboard(),
            )
            return

        offer_rate(
            chat_id,
            user_id,
            panel,
            result.rate,
            result.provider,
            result.fallback,
            result.fetched_at,
        )

    def offer_rate(
        chat_id: int,
        user_id: int,
        message_id: int,
        rate: Decimal,
        source: str,
        fallback: bool,
        fetched_at: datetime,
    ) -> None:
        state = states.get(chat_id) or {}
        states.set(
            chat_id,
            "rate_confirm",
            user_id=user_id,
            panel_id=message_id,
            from_country=state["from_country"],
            from_currency=state["from_currency"],
            to_country=state["to_country"],
            to_currency=state["to_currency"],
            rate=str(rate),
            rate_source=source,
            rate_manual=False,
        )
        from_currency = state["from_currency"]
        to_currency = state["to_currency"]
        if source == "same":
            text = (
                f"💱 {from_currency} и {to_currency} — одна валюта.\n"
                "Курс 1:1.\n\nПодходит?"
            )
        else:
            label = source_label(source) + (" — резервный источник" if fallback else "")
            text = (
                f"💱 Курс: 1 {to_currency} = {format_rate(rate)} {from_currency}\n"
                f"Источник: {label}\n"
                f"Обновлён: {fetched_at:%d.%m.%Y %H:%M} UTC\n\n"
                "Подходит?"
            )
        render(chat_id, message_id, text, rate_keyboard())

    def ask_initial_amount(chat_id: int, message_id: int | None) -> None:
        state = states.get(chat_id) or {}
        states.set(chat_id, "initial_amount", **state)
        from_currency = state["from_currency"]
        render(
            chat_id,
            message_id,
            "💰 Сколько берёте в поездку?\n\n"
            f"Введите начальную сумму в {from_currency}:",
            cancel_keyboard(),
        )

    def create_trip_from_state(chat_id: int, user_id: int, amount: Decimal) -> None:
        state = states.get(chat_id) or {}
        try:
            trip = db.create_trip(
                user_id,
                from_country=state["from_country"],
                from_currency=state["from_currency"],
                to_country=state["to_country"],
                to_currency=state["to_currency"],
                rate=Decimal(state["rate"]),
                initial_home=amount,
                rate_source=state["rate_source"],
                rate_manual=bool(state.get("rate_manual")),
            )
        except (KeyError, ValueError) as exc:
            states.clear(chat_id)
            bot.send_message(
                chat_id,
                f"Не получилось создать поездку: {exc}. Начните заново — /newtrip.",
                reply_markup=main_menu(),
            )
            return

        states.clear(chat_id)
        log.info("Пользователь %s создал поездку %s", user_id, trip.id)
        bot.send_message(
            chat_id,
            "✅ Поездка создана!\n\n"
            f"{trip_titles(trip)}\n"
            f"Начальный баланс: {format_money(trip.balance_foreign, trip.to_currency)} = "
            f"{format_money(trip.balance_home, trip.from_currency)}\n"
            f"Курс: {rate_line(trip)}\n\n"
            f"Отправьте число — это расход в {trip.to_currency}. Например: 50",
            reply_markup=main_menu(),
        )

    # --- баланс, история, поездки, курс, статус ----------------------------

    def show_balance(chat_id: int, message_id: int | None, user_id: int) -> None:
        trip = db.get_active_trip(user_id)
        if not trip:
            no_active_trip(chat_id, message_id)
            return
        spent_foreign, spent_home, _ = db.expenses_total(trip.id)
        render(
            chat_id,
            message_id,
            balance_text(trip, spent_foreign, spent_home),
            main_menu(),
        )

    def show_history(chat_id: int, message_id: int | None, user_id: int) -> None:
        trip = db.get_active_trip(user_id)
        if not trip:
            no_active_trip(chat_id, message_id)
            return
        expenses = db.list_expenses(trip.id, limit=20)
        total_foreign, total_home, total_count = db.expenses_total(trip.id)
        render(
            chat_id,
            message_id,
            history_text(trip, expenses, total_foreign, total_home, total_count),
            main_menu(),
        )

    def show_trips(chat_id: int, message_id: int | None, user_id: int) -> None:
        trips = db.list_trips(user_id)
        active = db.get_active_trip(user_id)
        render(
            chat_id,
            message_id,
            trips_text(trips, active.id if active else None),
            trips_keyboard(trips, active.id if active else None),
        )

    def begin_set_rate(chat_id: int, user_id: int, message_id: int | None) -> None:
        trip = db.get_active_trip(user_id)
        if not trip:
            no_active_trip(chat_id, message_id)
            return
        states.set(chat_id, "new_rate", user_id=user_id, panel_id=message_id)
        render(
            chat_id,
            message_id,
            f"Текущий курс: {rate_line(trip)}\n\n"
            f"Введите новый курс — сколько {trip.from_currency} стоит "
            f"1 {trip.to_currency}:",
            cancel_keyboard(),
        )

    def show_status(chat_id: int, message_id: int | None, user_id: int) -> None:
        active = db.get_active_trip(user_id)
        render(
            chat_id,
            message_id,
            status_text(api.status(), db.count(), active),
            main_menu(),
        )

    # --- расходы ------------------------------------------------------------

    def offer_expense(chat_id: int, user_id: int, amount: Decimal) -> None:
        trip = db.get_active_trip(user_id)
        if not trip:
            bot.send_message(
                chat_id,
                "Сначала создайте путешествие — тогда я смогу учитывать расходы.",
                reply_markup=main_menu(),
            )
            return
        amount_home = home_from_foreign(amount, trip.rate)
        states.set(
            chat_id,
            "expense_confirm",
            user_id=user_id,
            trip_id=trip.id,
            amount=str(amount),
            amount_home=str(amount_home),
        )
        bot.send_message(
            chat_id,
            expense_offer_text(trip, amount, amount_home),
            reply_markup=expense_keyboard(),
        )

    def add_pending_expense(chat_id: int, user_id: int, message_id: int) -> None:
        state = states.get(chat_id) or {}
        if state.get("step") != "expense_confirm":
            render(
                chat_id,
                message_id,
                "Эта сумма уже неактуальна. Отправьте число ещё раз.",
                main_menu(),
            )
            return
        trip = db.get_trip(int(state["trip_id"]))
        if trip is None or trip.user_id != user_id:
            states.clear(chat_id)
            render(chat_id, message_id, "Поездка не найдена.", main_menu())
            return
        amount = Decimal(state["amount"])
        expense, updated = db.add_expense(trip.id, amount)
        states.clear(chat_id)
        log.info("Пользователь %s учёл расход %.2f в поездке %s", user_id, amount, trip.id)
        render(
            chat_id,
            message_id,
            expense_done_text(updated, amount, expense.amount_home),
            main_menu(),
        )

    # --- callback-кнопки ----------------------------------------------------

    @bot.callback_query_handler(func=lambda call: True)
    def on_callback(call) -> None:
        try:
            route_callback(call)
        except Exception:  # noqa: BLE001 — бот не должен падать из-за одного клика
            log.exception("Ошибка обработки callback %r", getattr(call, "data", None))
            answer(call, "Что-то пошло не так")
            try:
                bot.send_message(
                    call.message.chat.id,
                    "⚠️ Внутренняя ошибка. Попробуйте ещё раз или отправьте /start.",
                )
            except ApiTelegramException:
                pass

    def route_callback(call) -> None:
        answer(call)
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        user_id = call.from_user.id
        data = call.data or ""

        if data.startswith("menu:"):
            action = data.split(":", 1)[1]
            states.clear(chat_id)
            if action == "home":
                send_home(chat_id, message_id, user_id)
            elif action == "new":
                begin_new_trip(chat_id, user_id, message_id)
            elif action == "trips":
                show_trips(chat_id, message_id, user_id)
            elif action == "balance":
                show_balance(chat_id, message_id, user_id)
            elif action == "history":
                show_history(chat_id, message_id, user_id)
            elif action == "rate":
                begin_set_rate(chat_id, user_id, message_id)
            elif action == "status":
                show_status(chat_id, message_id, user_id)
            elif action == "help":
                render(chat_id, message_id, help_text(), main_menu())
            return

        if data == "new:cancel":
            states.clear(chat_id)
            render(chat_id, message_id, "Отменено.", main_menu())
            return

        if data == "new:rate_ok":
            state = states.get(chat_id) or {}
            if state.get("step") != "rate_confirm":
                render(chat_id, message_id, "Шаг устарел. Начните заново — /newtrip.", main_menu())
                return
            states.set(chat_id, "initial_amount", **state)
            ask_initial_amount(chat_id, message_id)
            return

        if data == "new:rate_manual":
            state = states.get(chat_id) or {}
            if "from_currency" not in state:
                render(chat_id, message_id, "Шаг устарел. Начните заново — /newtrip.", main_menu())
                return
            states.set(
                chat_id,
                "manual_rate",
                user_id=user_id,
                panel_id=message_id,
                from_country=state["from_country"],
                from_currency=state["from_currency"],
                to_country=state["to_country"],
                to_currency=state["to_currency"],
            )
            render(
                chat_id,
                message_id,
                "✏️ Введите курс вручную:\n"
                f"сколько {state['from_currency']} стоит 1 {state['to_currency']}?\n"
                "Например: 104,5",
                cancel_keyboard(),
            )
            return

        if data.startswith("trip:switch:"):
            try:
                trip_id = int(data.rsplit(":", 1)[1])
            except ValueError:
                return
            trip = db.set_active_trip(user_id, trip_id)
            if trip is None:
                answer(call, "Поездка не найдена")
                return
            answer(call, f"Активная поездка: {trip.to_country}")
            show_balance(chat_id, message_id, user_id)
            return

        if data == "exp:yes":
            add_pending_expense(chat_id, user_id, message_id)
            return

        if data == "exp:no":
            states.clear(chat_id)
            render(chat_id, message_id, "Хорошо, расход не записан.", main_menu())
            return

        if data.startswith("new:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                return
            _, stage, key = parts
            if key == "other":
                render(
                    chat_id,
                    message_id,
                    "Напишите название страны или трёхбуквенный код валюты "
                    "(например, Перу или PEN).",
                    cancel_keyboard(),
                )
                return
            country = get_country(key) or find_country(key)
            if country is None:
                render(chat_id, message_id, "Не знаю такую страну. Попробуйте ещё раз.", main_menu())
                return
            if stage == "from":
                handle_from(chat_id, user_id, message_id, country)
            elif stage == "to":
                handle_to(chat_id, user_id, message_id, country)
            return

    # --- текстовые сообщения ------------------------------------------------

    @bot.message_handler(content_types=["text"])
    def on_text(message) -> None:
        chat_id, user_id = user_of(message)
        text = (message.text or "").strip()

        if text.startswith("/"):
            bot.send_message(
                chat_id,
                "Неизвестная команда. Список команд — /help.",
                reply_markup=main_menu(),
            )
            return

        state = states.get(chat_id) or {}
        step = state.get("step")
        panel_id = state.get("panel_id")

        if step in {"from_country", "to_country"}:
            country = find_country(text)
            if country is None:
                bot.send_message(
                    chat_id,
                    "Не нашёл такую страну. Напишите название иначе "
                    "(например, Италия) или код валюты (например, EUR).",
                )
                return
            if step == "from_country":
                handle_from(chat_id, user_id, panel_id, country)
            else:
                handle_to(chat_id, user_id, panel_id, country)
            return

        if step in {"manual_rate", "new_rate"}:
            rate = parse_rate(text)
            if rate is None:
                bot.send_message(
                    chat_id,
                    "Курс должен быть положительным числом, например 104,5.",
                )
                return
            if step == "manual_rate":
                state.update(
                    step="initial_amount",
                    rate=str(rate),
                    rate_source="manual",
                    rate_manual=True,
                )
                states.set(chat_id, "initial_amount", **state)
                from_currency = state["from_currency"]
                to_currency = state["to_currency"]
                render(
                    chat_id,
                    panel_id,
                    f"Курс принят: 1 {to_currency} = {format_rate(rate)} {from_currency}\n\n"
                    f"Введите начальную сумму в {from_currency}:",
                    cancel_keyboard(),
                )
                return

            trip = db.get_active_trip(user_id)
            if trip is None:
                states.clear(chat_id)
                no_active_trip(chat_id, panel_id)
                return
            updated = db.update_rate(trip.id, rate, source="manual", manual=True)
            states.clear(chat_id)
            spent_foreign, spent_home, _ = db.expenses_total(updated.id)
            render(
                chat_id,
                panel_id,
                f"✅ Курс обновлён: {rate_line(updated)}\n\n"
                + balance_text(updated, spent_foreign, spent_home),
                main_menu(),
            )
            return

        if step == "initial_amount":
            amount = parse_amount(text)
            if amount is None:
                bot.send_message(
                    chat_id,
                    "Введите положительную сумму, например 200000 или 200 000,50.",
                )
                return
            create_trip_from_state(chat_id, user_id, amount)
            return

        if step == "rate_error":
            bot.send_message(
                chat_id,
                "Введите курс вручную или нажмите кнопку под сообщением с ошибкой.",
            )
            return

        if step == "expense_confirm":
            # Пользователь прислал новое число, не ответив на прошлое предложение.
            pass

        amount = parse_amount(text)
        if amount is None:
            bot.send_message(
                chat_id,
                "Не понял. Отправьте число — это будет расход, "
                "или выберите действие в меню.",
                reply_markup=main_menu(),
            )
            return

        offer_expense(chat_id, user_id, amount)

    return bot


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    try:
        token = require_telegram_token(settings)
    except ConfigError as exc:
        print(f"Не удалось запустить бота: {exc}")
        return 2

    database = Database(settings.database_path)
    api = CurrencyAPI(settings)
    bot = build_bot(token, database, api, settings)

    print(f"Бот запущен. Провайдер курсов: {settings.provider_title}.")
    print(f"База данных: {settings.database_path}")
    if settings.provider == PROVIDER_EXCHANGERATE and not settings.api_key:
        print(
            "Внимание: CURRENCY_API_KEY не задан. "
            "Будет использован резервный open.er-api.com."
        )
    try:
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=25)
    except KeyboardInterrupt:
        print("Остановлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
