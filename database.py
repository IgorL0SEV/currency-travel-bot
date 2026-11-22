"""Хранение путешествий и расходов в SQLite.

Деньги и курсы хранятся строками (TEXT), чтобы не терять точность Decimal.
Каждая запись привязана к пользователю Telegram: чужие поездки недоступны.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from money import money, rate_value, to_decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    active_trip_id INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    from_country    TEXT NOT NULL,
    from_currency   TEXT NOT NULL,
    to_country      TEXT NOT NULL,
    to_currency     TEXT NOT NULL,
    rate            TEXT NOT NULL,
    rate_source     TEXT NOT NULL,
    rate_manual     INTEGER NOT NULL DEFAULT 0,
    initial_home    TEXT NOT NULL,
    balance_foreign TEXT NOT NULL,
    balance_home    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id        INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    amount_foreign TEXT NOT NULL,
    amount_home    TEXT NOT NULL,
    rate           TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trips_user ON trips(user_id, id);
CREATE INDEX IF NOT EXISTS idx_expenses_trip ON expenses(trip_id, id);
"""


@dataclass(frozen=True)
class Trip:
    id: int
    user_id: int
    from_country: str
    from_currency: str
    to_country: str
    to_currency: str
    rate: Decimal
    rate_source: str
    rate_manual: bool
    initial_home: Decimal
    balance_foreign: Decimal
    balance_home: Decimal
    created_at: str

    @property
    def title(self) -> str:
        return f"{self.from_country} → {self.to_country}"


@dataclass(frozen=True)
class Expense:
    id: int
    trip_id: int
    amount_foreign: Decimal
    amount_home: Decimal
    rate: Decimal
    created_at: str


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _trip(row: sqlite3.Row) -> Trip:
    return Trip(
        id=row["id"],
        user_id=row["user_id"],
        from_country=row["from_country"],
        from_currency=row["from_currency"],
        to_country=row["to_country"],
        to_currency=row["to_currency"],
        rate=to_decimal(row["rate"]),
        rate_source=row["rate_source"],
        rate_manual=bool(row["rate_manual"]),
        initial_home=to_decimal(row["initial_home"]),
        balance_foreign=to_decimal(row["balance_foreign"]),
        balance_home=to_decimal(row["balance_home"]),
        created_at=row["created_at"],
    )


def _expense(row: sqlite3.Row) -> Expense:
    return Expense(
        id=row["id"],
        trip_id=row["trip_id"],
        amount_foreign=to_decimal(row["amount_foreign"]),
        amount_home=to_decimal(row["amount_home"]),
        rate=to_decimal(row["rate"]),
        created_at=row["created_at"],
    )


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version = 1")

    # --- пользователи -------------------------------------------------

    def get_or_create_user(self, user_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
                (int(user_id), _now()),
            )

    # --- поездки ------------------------------------------------------

    def create_trip(
        self,
        user_id: int,
        *,
        from_country: str,
        from_currency: str,
        to_country: str,
        to_currency: str,
        rate: Decimal,
        initial_home: Decimal,
        rate_source: str,
        rate_manual: bool = False,
    ) -> Trip:
        rate = rate_value(rate)
        initial_home = money(initial_home)
        if rate <= 0:
            raise ValueError("Курс должен быть положительным")
        if initial_home <= 0:
            raise ValueError("Начальная сумма должна быть положительной")

        # Домашняя сумма — то, что ввёл пользователь, она и есть стартовый баланс.
        # Валюта поездки считается по курсу и округляется до копеек.
        balance_home = initial_home
        balance_foreign = money(initial_home / rate)

        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
                (int(user_id), _now()),
            )
            cursor = connection.execute(
                """INSERT INTO trips (
                       user_id, from_country, from_currency, to_country, to_currency,
                       rate, rate_source, rate_manual, initial_home,
                       balance_foreign, balance_home, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(user_id),
                    from_country,
                    from_currency,
                    to_country,
                    to_currency,
                    str(rate),
                    rate_source,
                    int(rate_manual),
                    str(initial_home),
                    str(balance_foreign),
                    str(balance_home),
                    _now(),
                ),
            )
            trip_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE users SET active_trip_id = ? WHERE user_id = ?",
                (trip_id, int(user_id)),
            )
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return _trip(row)

    def get_trip(self, trip_id: int) -> Trip | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
        return _trip(row) if row else None

    def list_trips(self, user_id: int) -> list[Trip]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM trips WHERE user_id = ? ORDER BY id DESC",
                (int(user_id),),
            ).fetchall()
        return [_trip(row) for row in rows]

    def get_active_trip(self, user_id: int) -> Trip | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT t.* FROM users u
                   JOIN trips t ON t.id = u.active_trip_id
                   WHERE u.user_id = ? AND t.user_id = ?""",
                (int(user_id), int(user_id)),
            ).fetchone()
        return _trip(row) if row else None

    def set_active_trip(self, user_id: int, trip_id: int) -> Trip | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM trips WHERE id = ? AND user_id = ?",
                (int(trip_id), int(user_id)),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE users SET active_trip_id = ? WHERE user_id = ?",
                (int(trip_id), int(user_id)),
            )
        return _trip(row)

    def update_rate(self, trip_id: int, rate: Decimal, *, source: str, manual: bool) -> Trip:
        rate = rate_value(rate)
        if rate <= 0:
            raise ValueError("Курс должен быть положительным")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
            if row is None:
                raise ValueError("Поездка не найдена")
            balance_foreign = to_decimal(row["balance_foreign"])
            balance_home = money(balance_foreign * rate)
            connection.execute(
                """UPDATE trips
                   SET rate = ?, rate_source = ?, rate_manual = ?, balance_home = ?
                   WHERE id = ?""",
                (str(rate), source, int(manual), str(balance_home), int(trip_id)),
            )
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
        return _trip(row)

    # --- расходы ------------------------------------------------------

    def add_expense(self, trip_id: int, amount_foreign: Decimal) -> tuple[Expense, Trip]:
        amount_foreign = money(amount_foreign)
        if amount_foreign <= 0:
            raise ValueError("Сумма расхода должна быть положительной")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
            if row is None:
                raise ValueError("Поездка не найдена")
            trip = _trip(row)
            amount_home = money(amount_foreign * trip.rate)
            balance_foreign = money(trip.balance_foreign - amount_foreign)
            balance_home = money(trip.balance_home - amount_home)
            created_at = _now()
            cursor = connection.execute(
                """INSERT INTO expenses (trip_id, amount_foreign, amount_home, rate, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (int(trip_id), str(amount_foreign), str(amount_home), str(trip.rate), created_at),
            )
            connection.execute(
                "UPDATE trips SET balance_foreign = ?, balance_home = ? WHERE id = ?",
                (str(balance_foreign), str(balance_home), int(trip_id)),
            )
            expense = Expense(
                id=int(cursor.lastrowid),
                trip_id=int(trip_id),
                amount_foreign=amount_foreign,
                amount_home=amount_home,
                rate=trip.rate,
                created_at=created_at,
            )
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
        return expense, _trip(row)

    def list_expenses(self, trip_id: int, limit: int = 50) -> list[Expense]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM expenses WHERE trip_id = ? ORDER BY id DESC LIMIT ?",
                (int(trip_id), int(limit)),
            ).fetchall()
        return [_expense(row) for row in rows]

    def expenses_total(self, trip_id: int) -> tuple[Decimal, Decimal, int]:
        """Сумма расходов в валюте поездки и в домашней валюте + количество.

        Считаем по строкам через Decimal, а не через SQL SUM(REAL),
        чтобы не накопить ошибку округления.
        """
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT amount_foreign, amount_home FROM expenses WHERE trip_id = ?",
                (int(trip_id),),
            ).fetchall()
        total_foreign = sum((to_decimal(r["amount_foreign"]) for r in rows), Decimal("0"))
        total_home = sum((to_decimal(r["amount_home"]) for r in rows), Decimal("0"))
        return money(total_foreign), money(total_home), len(rows)

    def count(self) -> dict[str, int]:
        with self._connection() as connection:
            users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            trips = connection.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
            expenses = connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        return {"users": int(users), "trips": int(trips), "expenses": int(expenses)}
