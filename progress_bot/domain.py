from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


# Whitespace may surround a report but is not part of its wire format.
REPORT_PATTERN = re.compile(r"^\s*(?P<correction>\*)?(?P<personal>\d+)/(?P<location>\d+)\s*$")


@dataclass(frozen=True)
class ParsedReport:
    personal_units: int
    location_units: int
    is_correction: bool


@dataclass(frozen=True)
class EffectiveReport:
    user_id: int
    personal_units: int
    location_units: int
    received_at: datetime
    report_count: int
    display_name: str | None = None


@dataclass(frozen=True)
class Summary:
    business_day: str
    generated_at: datetime
    target: int | None
    location_units: int | None
    pace_expected: float | None
    pace_variance: float | None
    participants: tuple[EffectiveReport, ...]
    conflicting_location_values: dict[int, int]


def parse_report(text: str | None) -> ParsedReport | None:
    if text is None:
        return None
    match = REPORT_PATTERN.fullmatch(text)
    if not match:
        return None
    return ParsedReport(
        personal_units=int(match["personal"]),
        location_units=int(match["location"]),
        is_correction=bool(match["correction"]),
    )


def format_user_label(user_id: int, display_name: str | None = None) -> str:
    """Return the manager-friendly user label, while retaining the Telegram ID."""
    return f"{display_name} ({user_id})" if display_name else str(user_id)


def business_day_at(moment: datetime, tz: ZoneInfo, reset_time: time) -> str:
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    local = moment.astimezone(tz)
    if local.timetz().replace(tzinfo=None) < reset_time:
        local -= timedelta(days=1)
    return local.date().isoformat()


def business_day_start(day: str, tz: ZoneInfo, reset_time: time) -> datetime:
    return datetime.combine(datetime.fromisoformat(day).date(), reset_time, tzinfo=tz)


def make_summary(
    business_day: str,
    generated_at: datetime,
    tz: ZoneInfo,
    reset_time: time,
    target: int | None,
    reports: list[EffectiveReport],
    location_control: int | None = None,
) -> Summary:
    reports = sorted(reports, key=lambda item: item.user_id)
    # Different reports may legitimately repeat an older location total or
    # advance it.  The control total is therefore its daily high-water mark.
    location = location_control
    if location is None and reports:
        location = max(item.location_units for item in reports)
    expected = variance = None
    if target is not None and location is not None:
        start = business_day_start(business_day, tz, reset_time)
        elapsed = max(0.0, (generated_at.astimezone(tz) - start).total_seconds())
        expected = target * min(elapsed / timedelta(days=1).total_seconds(), 1.0)
        variance = location - expected
    return Summary(business_day, generated_at, target, location, expected, variance, tuple(reports), {})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
