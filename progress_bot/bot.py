from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .domain import business_day_start, format_user_label, parse_report, utc_now
from .presentation import format_summary
from .service import ProgressService
from .storage import Store
from .translations import Translator


LOGGER = logging.getLogger(__name__)


class TelegramProgressBot:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.translator = Translator(settings.language)
        self.service = ProgressService(settings, store, self.translator)

    def is_owner_pm(self, update: Update) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        return bool(chat and user and chat.type == "private" and user.id == self.settings.owner_user_id)

    async def send_owner(self, bot, message: str, *, parse_mode: str | None = None) -> None:
        for attempt in range(3):
            try:
                await bot.send_message(chat_id=self.settings.owner_user_id, text=message, parse_mode=parse_mode)
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

    def log_rejected_report(self, message, user, chat, reason: str) -> None:
        """Record rejected text without allowing an unusually long message to flood logs."""
        text = message.text or ""
        if len(text) > 500:
            text = text[:500] + "... [truncated]"
        LOGGER.info(
            "rejected report reason=%s chat_id=%s topic_id=%s message_id=%s user_id=%s text=%r",
            reason,
            chat.id,
            message.message_thread_id,
            message.message_id,
            user.id,
            text,
        )

    @staticmethod
    def sender_display_name(user) -> str | None:
        """Use the current Telegram profile name, omitting absent name parts."""
        parts = (getattr(user, "first_name", None), getattr(user, "last_name", None))
        name = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        return name or None

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner_pm(update):
            return
        assert update.effective_message is not None
        name = (update.effective_message.text or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
        now = utc_now()
        if name == "/help":
            await update.effective_message.reply_text(self.translator.text("help"))
        elif name == "/users":
            users = self.store.users()
            labels = ", ".join(format_user_label(user_id, display_name) for user_id, display_name in users)
            await update.effective_message.reply_text(self.translator.text("monitored_users", users=labels or self.translator.text("none")))
        elif name in {"/adduser", "/removeuser"}:
            has_valid_id = bool(context.args) and context.args[0].isdigit() and int(context.args[0]) > 0
            has_valid_arguments = has_valid_id and (name == "/adduser" or len(context.args) == 1)
            if not has_valid_arguments:
                key = "usage_adduser" if name == "/adduser" else "usage_removeuser"
                await update.effective_message.reply_text(self.translator.text(key))
                return
            user_id = int(context.args[0])
            display_name = " ".join(context.args[1:]).strip() or None
            changed = self.store.add_user(user_id, now, display_name) if name == "/adduser" else self.store.remove_user(user_id)
            label = format_user_label(user_id, display_name)
            if changed:
                key = "added_or_updated" if name == "/adduser" else "removed"
                response = self.translator.text(key, label=label)
            else:
                response = self.translator.text("no_change", label=label)
            await update.effective_message.reply_text(response)
        elif name == "/target":
            day = self.service.business_day(now)
            if not context.args:
                target = self.store.target(day)
                key = "target_not_set" if target is None else "target_show"
                await update.effective_message.reply_text(self.translator.text(key, target=target))
            elif len(context.args) == 1 and context.args[0].isdigit():
                units = int(context.args[0])
                self.service.set_target(units, now)
                await update.effective_message.reply_text(self.translator.text("target_set", day=day, units=units))
            else:
                await update.effective_message.reply_text(self.translator.text("usage_target"))
        elif name == "/status":
            await self.send_owner(context.bot, self.format_summary(now), parse_mode=ParseMode.HTML)

    def format_summary(self, now: datetime) -> str:
        return format_summary(
            self.service.summary(now),
            self.translator,
            self.settings.card_percent_bad_threshold,
        )

    async def report_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if not message or not user or not chat:
            return
        if chat.id != self.settings.group_chat_id:
            self.log_rejected_report(message, user, chat, "wrong_chat")
            return
        if message.message_thread_id != self.settings.topic_id:
            self.log_rejected_report(message, user, chat, "wrong_topic")
            return
        if not self.settings.accept_all_topic_users and not self.store.is_monitored(user.id):
            self.log_rejected_report(message, user, chat, "unmonitored_user")
            return
        parsed = parse_report(message.text)
        if parsed is None:
            self.log_rejected_report(message, user, chat, "invalid_report_format")
            return
        now = utc_now()
        # Keep the summary label in sync with the sender's Telegram profile.
        # This runs only after the monitoring check, so restricted mode cannot
        # be bypassed by sending a report.
        self.store.add_user(user.id, now, self.sender_display_name(user))
        result, alerts = self.service.submit(user.id, chat.id, message.message_id, parsed, now)
        day = self.service.business_day(now)
        for index, alert in enumerate(alerts):
            fingerprint = f"report:{chat.id}:{message.message_id}:{index}"
            if self.store.create_alert(fingerprint, day, alert, now):
                await self.send_owner(context.bot, self.translator.text("alert_prefix", message=alert))
        if result.status == "accepted":
            LOGGER.info("accepted report message_id=%s user_id=%s", message.message_id, user.id)
        else:
            self.log_rejected_report(message, user, chat, result.status)

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
        slot = self.store.oldest_pending_summary_slot() or self.summary_slot(now)
        if not self.store.claim_summary_slot(slot, now):
            return
        await self.send_owner(context.bot, self.format_summary(now), parse_mode=ParseMode.HTML)
        self.store.mark_summary_sent(slot, utc_now())

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Unhandled Telegram update error", exc_info=context.error)
        try:
            await self.send_owner(context.bot, self.translator.text("internal_error"))
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
