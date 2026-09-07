from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .domain import business_day_start, parse_report, utc_now
from .presentation import format_summary
from .service import ProgressService
from .storage import Store

LOGGER = logging.getLogger(__name__)
HELP = """Progress bot commands:
/adduser <telegram_id> — monitor a user
/removeuser <telegram_id> — stop monitoring a user
/users — list monitored users
/target <non-negative integer> — set today’s target
/target — show today’s target
/status — send a current summary"""


class TelegramProgressBot:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.service = ProgressService(settings, store)

    def is_owner_pm(self, update: Update) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        return bool(chat and user and chat.type == "private" and user.id == self.settings.owner_user_id)

    async def send_owner(self, bot, message: str) -> None:
        for attempt in range(3):
            try:
                await bot.send_message(chat_id=self.settings.owner_user_id, text=message)
                return
            except RetryAfter as error:
                if attempt == 2:
                    raise
                retry_after = error.retry_after
                seconds = retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
                await asyncio.sleep(seconds + 1)
            except NetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner_pm(update):
            return
        assert update.effective_message is not None
        name = (update.effective_message.text or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
        now = utc_now()
        if name == "/help":
            await update.effective_message.reply_text(HELP)
        elif name == "/users":
            users = self.store.users()
            await update.effective_message.reply_text("Monitored users: " + (", ".join(map(str, users)) if users else "none"))
        elif name in {"/adduser", "/removeuser"}:
            if len(context.args) != 1 or not context.args[0].isdigit() or int(context.args[0]) <= 0:
                await update.effective_message.reply_text(f"Usage: {name} <positive telegram_id>")
                return
            user_id = int(context.args[0])
            changed = self.store.add_user(user_id, now) if name == "/adduser" else self.store.remove_user(user_id)
            verb = "Added" if name == "/adduser" else "Removed"
            await update.effective_message.reply_text(f"{verb} {user_id}." if changed else f"No change: {user_id} was already in that state.")
        elif name == "/target":
            day = self.service.business_day(now)
            if not context.args:
                target = self.store.target(day)
                await update.effective_message.reply_text("Today’s target is not set." if target is None else f"Today’s target: {target}.")
            elif len(context.args) == 1 and context.args[0].isdigit():
                units = int(context.args[0])
                self.service.set_target(units, now)
                await update.effective_message.reply_text(f"Target for {day} set to {units}.")
            else:
                await update.effective_message.reply_text("Usage: /target <non-negative integer>")
        elif name == "/status":
            await self.send_owner(context.bot, format_summary(self.service.summary(now)))

    async def report_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not update.effective_chat:
            return
        if update.effective_chat.id != self.settings.group_chat_id or message.message_thread_id != self.settings.topic_id:
            return
        if not self.store.is_monitored(user.id):
            return
        parsed = parse_report(message.text)
        if parsed is None:
            return
        now = utc_now()
        result, alerts = self.service.submit(user.id, update.effective_chat.id, message.message_id, parsed, now)
        day = self.service.business_day(now)
        for index, alert in enumerate(alerts):
            fingerprint = f"report:{update.effective_chat.id}:{message.message_id}:{index}"
            if self.store.create_alert(fingerprint, day, alert, now):
                await self.send_owner(context.bot, "⚠ " + alert)
        if result.status == "accepted":
            LOGGER.info("accepted report message_id=%s user_id=%s", message.message_id, user.id)

    def next_summary_time(self, now: datetime) -> datetime:
        day = self.service.business_day(now)
        start = business_day_start(day, self.settings.timezone, self.settings.daily_reset_time)
        interval = timedelta(minutes=self.settings.summary_interval_minutes)
        local_now = now.astimezone(self.settings.timezone)
        elapsed = local_now - start
        steps = int(elapsed.total_seconds() // interval.total_seconds()) + 1
        return start + steps * interval

    def summary_slot(self, now: datetime) -> str:
        """Return the logical, reset-anchored interval identity for *now*."""
        day = self.service.business_day(now)
        start = business_day_start(day, self.settings.timezone, self.settings.daily_reset_time)
        interval = timedelta(minutes=self.settings.summary_interval_minutes)
        elapsed = now.astimezone(self.settings.timezone) - start
        steps = max(0, int(elapsed.total_seconds() // interval.total_seconds()))
        return (start + steps * interval).isoformat()

    async def scheduled_summary(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = utc_now()
        # Slot identity prevents duplicate sends after a restart while retaining
        # a pending entry for retries following transient API failures.
        slot = self.store.oldest_pending_summary_slot() or self.summary_slot(now)
        if not self.store.claim_summary_slot(slot, now):
            return
        await self.send_owner(context.bot, format_summary(self.service.summary(now)))
        self.store.mark_summary_sent(slot, utc_now())

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Unhandled Telegram update error", exc_info=context.error)
        try:
            await self.send_owner(context.bot, "⚠ Internal bot error; it was logged and the bot will continue processing updates.")
        except TelegramError:
            LOGGER.exception("Could not notify owner about bot error")

    async def post_init(self, application: Application) -> None:
        next_run = self.next_summary_time(utc_now())
        delay = max((next_run - utc_now().astimezone(self.settings.timezone)).total_seconds(), 1)
        application.job_queue.run_repeating(self.scheduled_summary, interval=self.settings.summary_interval_minutes * 60, first=delay, name="progress-summary")
        identity = await application.bot.get_me()
        LOGGER.info("bot ready as @%s; next summary at %s", identity.username, next_run.isoformat())

    def application(self) -> Application:
        app = Application.builder().token(self.settings.token).post_init(self.post_init).build()
        command_filter = filters.COMMAND
        app.add_handler(CommandHandler(["adduser", "removeuser", "users", "target", "status", "help"], self.command))
        app.add_handler(MessageHandler(filters.TEXT & ~command_filter, self.report_message))
        app.add_error_handler(self.error_handler)
        return app
