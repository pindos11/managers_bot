import asyncio
import logging
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from progress_bot.bot import TelegramProgressBot
from progress_bot.config import Settings
from progress_bot.domain import parse_report, utc_now
from progress_bot.presentation import format_summary
from progress_bot.storage import Store


class FakeMessage:
    def __init__(self, text: str, message_id: int, topic_id: int | None = None) -> None:
        self.text, self.message_id, self.message_thread_id = text, message_id, topic_id
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def make_bot(tmp_path: Path) -> TelegramProgressBot:
    settings = Settings("token", 1, -100, 5, ZoneInfo("Europe/Kyiv"), time(6), 60, tmp_path / "bot.sqlite")
    return TelegramProgressBot(settings, Store(settings.database_path))


def update(chat_id: int, user_id: int, message: FakeMessage, chat_type: str = "supergroup"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
    )


def test_only_the_configured_topic_and_monitored_user_are_processed(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    bot.store.add_user(10, now)
    context = SimpleNamespace(bot=FakeBot(), args=[])
    asyncio.run(bot.report_message(update(-99, 10, FakeMessage("1/1", 1, 5)), context))
    asyncio.run(bot.report_message(update(-100, 10, FakeMessage("1/1", 2, 4)), context))
    asyncio.run(bot.report_message(update(-100, 11, FakeMessage("1/1", 3, 5)), context))
    assert bot.service.summary(utc_now()).participants == ()
    asyncio.run(bot.report_message(update(-100, 10, FakeMessage("1/1", 4, 5)), context))
    assert bot.service.summary(utc_now()).participants[0].user_id == 10


def test_all_topic_users_mode_accepts_unmonitored_users(tmp_path: Path) -> None:
    settings = Settings(
        "token", 1, -100, 5, ZoneInfo("Europe/Kyiv"), time(6), 60,
        tmp_path / "bot.sqlite", accept_all_topic_users=True,
    )
    bot = TelegramProgressBot(settings, Store(settings.database_path))
    context = SimpleNamespace(bot=FakeBot(), args=[])
    asyncio.run(bot.report_message(update(-100, 99, FakeMessage("1/1", 1, 5)), context))
    assert bot.service.summary(utc_now()).participants[0].user_id == 99


def test_rejected_reports_are_logged_with_reason_and_text(tmp_path: Path, caplog) -> None:
    bot = make_bot(tmp_path)
    context = SimpleNamespace(bot=FakeBot(), args=[])
    caplog.set_level(logging.INFO, logger="progress_bot.bot")

    asyncio.run(bot.report_message(update(-100, 10, FakeMessage("not a report", 7, 5)), context))

    assert "reason=unmonitored_user" in caplog.text
    assert "text='not a report'" in caplog.text

    bot.store.add_user(10, datetime(2026, 9, 5, 8, tzinfo=timezone.utc))
    caplog.clear()
    asyncio.run(bot.report_message(update(-100, 10, FakeMessage("not a report", 8, 5)), context))

    assert "reason=invalid_report_format" in caplog.text
    assert "text='not a report'" in caplog.text


def test_commands_are_owner_private_message_only(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    fake_bot = FakeBot()
    message = FakeMessage("/target 9", 1)
    context = SimpleNamespace(bot=fake_bot, args=["9"])
    asyncio.run(bot.command(update(99, 2, message, "private"), context))
    assert not message.replies
    asyncio.run(bot.command(update(1, 1, message, "private"), context))
    assert len(message.replies) == 1
    assert message.replies[0].endswith("set to 9.")


def test_adduser_name_is_shown_in_users_and_summary(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    context = SimpleNamespace(bot=FakeBot(), args=["10", "Alice", "Smith"])
    message = FakeMessage("/adduser 10 Alice Smith", 1)
    asyncio.run(bot.command(update(1, 1, message, "private"), context))
    assert message.replies == ["Added or updated Alice Smith (10)."]
    assert bot.store.users() == [(10, "Alice Smith")]

    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    bot.service.submit(10, -100, 2, parse_report("1/2"), now)
    summary = bot.service.summary(now)
    assert summary.participants[0].display_name == "Alice Smith"
    assert "Alice Smith (10)" in format_summary(summary)


def test_summary_shows_card_share_and_marks_a_bad_share(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    bot.service.submit(10, -100, 2, parse_report("10/20/4/6"), now)

    text = format_summary(bot.service.summary(now), card_percent_bad_threshold=50)

    assert "card 4 (40.0%) ⚠️" in text


def test_summary_slot_is_anchored_to_daily_reset(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    before = datetime(2026, 9, 5, 6, 59, 50, tzinfo=ZoneInfo("Europe/Kyiv"))
    after = datetime(2026, 9, 5, 7, 0, 5, tzinfo=ZoneInfo("Europe/Kyiv"))
    assert bot.summary_slot(before).endswith("06:00:00+03:00")
    assert bot.summary_slot(after).endswith("07:00:00+03:00")
