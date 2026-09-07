from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from progress_bot.domain import EffectiveReport, business_day_at, make_summary, parse_report


KYIV = ZoneInfo("Europe/Kyiv")


def test_parse_report_accepts_surrounding_whitespace_and_correction() -> None:
    assert parse_report("  12/34 ") == parse_report("12/34")
    parsed = parse_report(" *12/34 ")
    assert parsed and parsed.is_correction and parsed.personal_units == 12 and parsed.location_units == 34


def test_parse_report_rejects_non_reports() -> None:
    for value in ("", "-1/2", "1/2 hello", "1", "*/2", "1.0/2", "1 /2", "1/ 2", "* 1/2", None):
        assert parse_report(value) is None


def test_business_day_uses_configured_reset_time() -> None:
    reset = time(6, 0)
    assert business_day_at(datetime(2026, 9, 5, 2, 59, tzinfo=timezone.utc), KYIV, reset) == "2026-09-04"
    assert business_day_at(datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc), KYIV, reset) == "2026-09-05"


def test_summary_uses_latest_location_and_calculates_pace() -> None:
    reports = [
        EffectiveReport(10, 2, 40, datetime(2026, 9, 5, 9, tzinfo=timezone.utc), 1),
        EffectiveReport(11, 3, 50, datetime(2026, 9, 5, 10, tzinfo=timezone.utc), 2),
    ]
    result = make_summary("2026-09-05", datetime(2026, 9, 5, 15, tzinfo=timezone.utc), KYIV, time(6), 120, reports)
    assert result.location_units == 50
    assert result.conflicting_location_values == {10: 40, 11: 50}
    assert result.pace_expected == 60
    assert result.pace_variance == -10
