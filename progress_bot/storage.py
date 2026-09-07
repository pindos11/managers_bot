from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from .domain import EffectiveReport, ParsedReport


@dataclass(frozen=True)
class RecordResult:
    status: str  # accepted, duplicate, orphan_correction
    report_id: int | None = None
    previous: EffectiveReport | None = None
    previous_location_control: int | None = None


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            with self.connection:
                yield self.connection

    def migrate(self) -> None:
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS monitored_users (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL,
                display_name TEXT
            );
            CREATE TABLE IF NOT EXISTS targets (
                business_day TEXT PRIMARY KEY,
                units INTEGER NOT NULL CHECK(units >= 0),
                set_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_day TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                personal_units INTEGER NOT NULL CHECK(personal_units >= 0),
                location_units INTEGER NOT NULL CHECK(location_units >= 0),
                is_correction INTEGER NOT NULL,
                superseded_by INTEGER REFERENCES reports(id),
                UNIQUE(chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS reports_current_idx
              ON reports(business_day, user_id, received_at DESC);
            CREATE TABLE IF NOT EXISTS owner_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                business_day TEXT NOT NULL,
                created_at TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summary_deliveries (
                slot TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('pending', 'sent')),
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(monitored_users)")}
            if "display_name" not in columns:
                db.execute("ALTER TABLE monitored_users ADD COLUMN display_name TEXT")

    def add_user(self, user_id: int, now: datetime, display_name: str | None = None) -> bool:
        with self.transaction() as db:
            if display_name is None:
                cursor = db.execute("INSERT OR IGNORE INTO monitored_users(user_id, added_at) VALUES (?, ?)", (user_id, now.isoformat()))
            else:
                cursor = db.execute("""INSERT INTO monitored_users(user_id, added_at, display_name) VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name""", (user_id, now.isoformat(), display_name))
            return cursor.rowcount == 1

    def remove_user(self, user_id: int) -> bool:
        with self.transaction() as db:
            cursor = db.execute("DELETE FROM monitored_users WHERE user_id = ?", (user_id,))
            return cursor.rowcount == 1

    def users(self) -> list[tuple[int, str | None]]:
        return [(int(row["user_id"]), row["display_name"]) for row in self.connection.execute(
            "SELECT user_id, display_name FROM monitored_users ORDER BY user_id"
        )]

    def is_monitored(self, user_id: int) -> bool:
        return self.connection.execute("SELECT 1 FROM monitored_users WHERE user_id = ?", (user_id,)).fetchone() is not None

    def set_target(self, business_day: str, units: int, now: datetime) -> None:
        with self.transaction() as db:
            db.execute("""INSERT INTO targets(business_day, units, set_at) VALUES (?, ?, ?)
                ON CONFLICT(business_day) DO UPDATE SET units=excluded.units, set_at=excluded.set_at""", (business_day, units, now.isoformat()))

    def target(self, business_day: str) -> int | None:
        row = self.connection.execute("SELECT units FROM targets WHERE business_day = ?", (business_day,)).fetchone()
        return None if row is None else int(row[0])

    def _current_report(self, db: sqlite3.Connection, business_day: str, user_id: int) -> sqlite3.Row | None:
        return db.execute("""SELECT * FROM reports WHERE business_day=? AND user_id=? AND superseded_by IS NULL
            ORDER BY received_at DESC, id DESC LIMIT 1""", (business_day, user_id)).fetchone()

    @staticmethod
    def _effective(row: sqlite3.Row, count: int = 0) -> EffectiveReport:
        name = row["display_name"] if "display_name" in row.keys() else None
        return EffectiveReport(row["user_id"], row["personal_units"], row["location_units"], datetime.fromisoformat(row["received_at"]), count, name)

    def record_report(self, business_day: str, user_id: int, chat_id: int, message_id: int, report: ParsedReport, now: datetime) -> RecordResult:
        with self.transaction() as db:
            duplicate = db.execute("SELECT id FROM reports WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
            if duplicate:
                return RecordResult("duplicate")
            previous_row = self._current_report(db, business_day, user_id)
            if report.is_correction and previous_row is None:
                return RecordResult("orphan_correction")
            # The location value is a day-wide control total.  Keep its
            # high-water mark in the immutable report history, rather than
            # process memory, so a restart cannot reset the rule.
            location_control = db.execute(
                "SELECT MAX(location_units) FROM reports WHERE business_day=?",
                (business_day,),
            ).fetchone()[0]
            cursor = db.execute("""INSERT INTO reports(business_day,user_id,chat_id,message_id,received_at,personal_units,location_units,is_correction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (business_day, user_id, chat_id, message_id, now.isoformat(), report.personal_units, report.location_units, int(report.is_correction)))
            report_id = int(cursor.lastrowid)
            if report.is_correction:
                db.execute("UPDATE reports SET superseded_by=? WHERE id=?", (report_id, previous_row["id"]))
            return RecordResult(
                "accepted",
                report_id,
                self._effective(previous_row) if previous_row else None,
                None if location_control is None else int(location_control),
            )

    def location_control(self, business_day: str) -> int | None:
        """Return the durable highest location total reported for this day."""
        row = self.connection.execute(
            "SELECT MAX(location_units) FROM reports WHERE business_day=?",
            (business_day,),
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def effective_reports(self, business_day: str) -> list[EffectiveReport]:
        rows = self.connection.execute("""
            WITH ranked AS (
              SELECT r.*, u.display_name, ROW_NUMBER() OVER (PARTITION BY r.user_id ORDER BY r.received_at DESC, r.id DESC) AS rank,
                COUNT(*) OVER (PARTITION BY r.user_id) AS report_count
              FROM reports r LEFT JOIN monitored_users u ON u.user_id = r.user_id
              WHERE business_day=? AND superseded_by IS NULL
            ) SELECT * FROM ranked WHERE rank=1 ORDER BY user_id
        """, (business_day,)).fetchall()
        return [self._effective(row, row["report_count"]) for row in rows]

    def create_alert(self, fingerprint: str, business_day: str, message: str, now: datetime) -> bool:
        with self.transaction() as db:
            cursor = db.execute("INSERT OR IGNORE INTO owner_alerts(fingerprint,business_day,created_at,message) VALUES (?, ?, ?, ?)", (fingerprint, business_day, now.isoformat(), message))
            return cursor.rowcount == 1

    def claim_summary_slot(self, slot: str, now: datetime) -> bool:
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO summary_deliveries(slot,status,created_at) VALUES (?, 'pending', ?)", (slot, now.isoformat()))
            # A failed send deliberately remains pending, so the next scheduled
            # execution retries it. JobQueue invokes this job serially.
            row = db.execute("SELECT status FROM summary_deliveries WHERE slot=?", (slot,)).fetchone()
            return row is not None and row["status"] == "pending"

    def oldest_pending_summary_slot(self) -> str | None:
        row = self.connection.execute("SELECT slot FROM summary_deliveries WHERE status='pending' ORDER BY slot LIMIT 1").fetchone()
        return None if row is None else str(row["slot"])

    def mark_summary_sent(self, slot: str, now: datetime) -> None:
        with self.transaction() as db:
            db.execute("UPDATE summary_deliveries SET status='sent', sent_at=? WHERE slot=?", (now.isoformat(), slot))
