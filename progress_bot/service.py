from __future__ import annotations

from datetime import datetime

from .config import Settings
from .domain import ParsedReport, Summary, business_day_at, format_user_label, make_summary
from .storage import RecordResult, Store
from .translations import Translator


class ProgressService:
    def __init__(self, settings: Settings, store: Store, translator: Translator | None = None) -> None:
        self.settings, self.store = settings, store
        self.translator = translator or Translator(settings.language)

    def business_day(self, now: datetime) -> str:
        return business_day_at(now, self.settings.timezone, self.settings.daily_reset_time)

    def submit(self, user_id: int, chat_id: int, message_id: int, report: ParsedReport, now: datetime) -> tuple[RecordResult, list[str]]:
        day = self.business_day(now)
        result = self.store.record_report(day, user_id, chat_id, message_id, report, now)
        alerts: list[str] = []
        user = next((format_user_label(item_id, name) for item_id, name in self.store.users() if item_id == user_id), str(user_id))
        if result.status == "orphan_correction":
            alerts.append(self.translator.text("alert_orphan_correction", user=user, day=day))
            return result, alerts
        if result.status != "accepted":
            return result, alerts
        if result.previous:
            if report.personal_units < result.previous.personal_units:
                alerts.append(self.translator.text(
                    "alert_personal_decreased", user=user, previous=result.previous.personal_units, current=report.personal_units
                ))
            for kind, current, previous in (
                ("card", report.card_units, result.previous.card_units),
                ("cash", report.cash_units, result.previous.cash_units),
            ):
                if current is not None and previous is not None and current < previous:
                    alerts.append(self.translator.text(
                        f"alert_{kind}_decreased", user=user, previous=previous, current=current
                    ))
            personal_change = report.personal_units - result.previous.personal_units
            location_change = report.location_units - result.previous.location_units
            if personal_change > 0 and location_change < personal_change:
                alerts.append(self.translator.text(
                    "alert_location_growth_insufficient",
                    user=user,
                    personal_change=personal_change,
                    location_change=location_change,
                ))
        if result.previous_location_control is not None and report.location_units < result.previous_location_control:
            alerts.append(self.translator.text(
                "alert_location_decreased", user=user, previous=result.previous_location_control, current=report.location_units
            ))
        return result, alerts

    def summary(self, now: datetime) -> Summary:
        day = self.business_day(now)
        return make_summary(
            day,
            now,
            self.settings.timezone,
            self.settings.daily_reset_time,
            self.store.target(day),
            self.store.effective_reports(day),
            self.store.location_control(day),
        )

    def set_target(self, units: int, now: datetime) -> str:
        self.store.set_target(self.business_day(now), units, now)
        return self.business_day(now)
