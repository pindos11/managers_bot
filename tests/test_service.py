from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from progress_bot.config import Settings
from progress_bot.domain import parse_report
from progress_bot.service import ProgressService
from progress_bot.storage import Store


def settings(db: Path) -> Settings:
    return Settings("test-token", 1, -100, 5, ZoneInfo("Europe/Kyiv"), time(6), 60, db)


def test_report_correction_and_decrease_alerts(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    service = ProgressService(settings(tmp_path / "bot.sqlite"), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    store.add_user(10, now)
    first, alerts = service.submit(10, -100, 1, parse_report("5/10"), now)
    assert first.status == "accepted" and not alerts
    second, alerts = service.submit(10, -100, 2, parse_report("*3/8"), now.replace(minute=5))
    assert second.status == "accepted"
    assert any("Personal total decreased" in alert for alert in alerts)
    assert any("Location control total decreased" in alert for alert in alerts)
    report = service.summary(now.replace(minute=10)).participants[0]
    assert (report.personal_units, report.location_units, report.report_count) == (3, 8, 1)


def test_orphan_correction_is_not_persisted(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    service = ProgressService(settings(tmp_path / "bot.sqlite"), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    result, alerts = service.submit(10, -100, 1, parse_report("*3/8"), now)
    assert result.status == "orphan_correction"
    assert service.summary(now).participants == ()
    assert "Ignored orphan" in alerts[0]


def test_location_control_high_water_mark_survives_database_restart(tmp_path: Path) -> None:
    db = tmp_path / "bot.sqlite"
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    store = Store(db)
    service = ProgressService(settings(db), store)
    for user in (10, 11):
        store.add_user(user, now)
    service.submit(10, -100, 1, parse_report("1/10"), now)
    service.submit(11, -100, 2, parse_report("2/11"), now.replace(minute=1))
    store.close()
    restored = ProgressService(settings(db), Store(db))
    summary = restored.summary(now.replace(minute=2))
    assert summary.location_units == 11
    assert summary.conflicting_location_values == {}


def test_location_control_must_keep_up_with_personal_growth(tmp_path: Path) -> None:
    db = tmp_path / "bot.sqlite"
    store = Store(db)
    service = ProgressService(settings(db), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)

    service.submit(10, -100, 1, parse_report("10/20"), now)
    _, insufficient_growth_alerts = service.submit(10, -100, 2, parse_report("12/20"), now.replace(minute=1))
    _, matching_growth_alerts = service.submit(10, -100, 3, parse_report("13/21"), now.replace(minute=2))
    _, other_user_decreased_alerts = service.submit(11, -100, 4, parse_report("1/19"), now.replace(minute=3))
    _, increased_alerts = service.submit(11, -100, 5, parse_report("2/25"), now.replace(minute=4))
    _, decreased_alerts = service.submit(10, -100, 6, parse_report("14/20"), now.replace(minute=5))

    assert any("did not keep up" in alert for alert in insufficient_growth_alerts)
    assert not matching_growth_alerts
    assert any("Location control total decreased" in alert for alert in other_user_decreased_alerts)
    assert not increased_alerts
    assert any("Location control total decreased" in alert for alert in decreased_alerts)
    assert service.summary(now.replace(minute=6)).location_units == 25

    store.close()
    restored = ProgressService(settings(db), Store(db))
    _, restart_alerts = restored.submit(11, -100, 7, parse_report("3/24"), now.replace(minute=7))
    assert any("Location control total decreased" in alert for alert in restart_alerts)


def test_duplicate_telegram_message_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    service = ProgressService(settings(tmp_path / "bot.sqlite"), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    first, _ = service.submit(10, -100, 7, parse_report("1/1"), now)
    duplicate, _ = service.submit(10, -100, 7, parse_report("1/1"), now)
    assert first.status == "accepted"
    assert duplicate.status == "duplicate"
    assert service.summary(now).participants[0].report_count == 1


def test_old_reports_preserve_payment_values_and_corrections_are_one_way(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    service = ProgressService(settings(tmp_path / "bot.sqlite"), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)

    assert service.submit(10, -100, 1, parse_report("1/10"), now)[0].status == "accepted"
    assert service.submit(10, -100, 2, parse_report("2/11/20/30"), now.replace(minute=1))[0].status == "accepted"
    assert service.submit(10, -100, 3, parse_report("3/12"), now.replace(minute=2))[0].status == "accepted"
    report = service.summary(now.replace(minute=2)).participants[0]
    assert (report.personal_units, report.location_units, report.card_units, report.cash_units) == (3, 12, 20, 30)

    # An old correction cannot erase payment values from the current report.
    rejected, _ = service.submit(10, -100, 4, parse_report("*2/11"), now.replace(minute=3))
    assert rejected.status == "incompatible_correction"
    assert service.summary(now.replace(minute=3)).participants[0].personal_units == 3

    other = Store(tmp_path / "other.sqlite")
    other_service = ProgressService(settings(tmp_path / "other.sqlite"), other)
    assert other_service.submit(10, -100, 1, parse_report("1/10"), now)[0].status == "accepted"
    accepted, _ = other_service.submit(10, -100, 2, parse_report("*2/11/20/30"), now.replace(minute=1))
    assert accepted.status == "accepted"
    amended = other_service.summary(now.replace(minute=1)).participants[0]
    assert (amended.personal_units, amended.card_units, amended.cash_units, amended.report_count) == (2, 20, 30, 1)


def test_target_and_summary_slot_persist(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    store.set_target("2026-09-05", 100, now)
    assert store.target("2026-09-05") == 100
    assert store.claim_summary_slot("slot", now)
    assert store.claim_summary_slot("slot", now)  # pending work is retryable
    assert store.oldest_pending_summary_slot() == "slot"
    store.mark_summary_sent("slot", now)
    assert not store.claim_summary_slot("slot", now)
    assert store.oldest_pending_summary_slot() is None
