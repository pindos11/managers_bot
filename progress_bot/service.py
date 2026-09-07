from __future__ import annotations

from datetime import datetime

from .config import Settings
from .domain import ParsedReport, Summary, business_day_at, make_summary
from .storage import RecordResult, Store


class ProgressService:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings, self.store = settings, store

    def business_day(self, now: datetime) -> str:
        return business_day_at(now, self.settings.timezone, self.settings.daily_reset_time)

    def submit(self, user_id: int, chat_id: int, message_id: int, report: ParsedReport, now: datetime) -> tuple[RecordResult, list[str]]:
        day = self.business_day(now)
        result = self.store.record_report(day, user_id, chat_id, message_id, report, now)
        alerts: list[str] = []
        if result.status == "orphan_correction":
            alerts.append(f"Ignored orphan correction from {user_id}: no report exists for {day}.")
            return result, alerts
        if result.status != "accepted":
            return result, alerts
        if result.previous:
            if report.personal_units < result.previous.personal_units:
                alerts.append(f"Personal total decreased for {user_id}: {result.previous.personal_units} → {report.personal_units}.")
            if report.location_units < result.previous.location_units:
                alerts.append(f"Location total decreased in {user_id}'s report: {result.previous.location_units} → {report.location_units}.")
        summary = self.summary(now)
        if summary.conflicting_location_values:
            details = ", ".join(f"{uid}={value}" for uid, value in summary.conflicting_location_values.items())
            alerts.append(f"Conflicting current location totals: {details}. Using latest report value {summary.location_units}.")
        return result, alerts

    def summary(self, now: datetime) -> Summary:
        day = self.business_day(now)
        return make_summary(day, now, self.settings.timezone, self.settings.daily_reset_time, self.store.target(day), self.store.effective_reports(day))

    def set_target(self, units: int, now: datetime) -> str:
        self.store.set_target(self.business_day(now), units, now)
        return self.business_day(now)
