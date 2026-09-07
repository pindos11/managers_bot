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
    assert any("Location total decreased" in alert for alert in alerts)
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


def test_latest_report_and_conflicts_survive_database_restart(tmp_path: Path) -> None:
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
    assert summary.conflicting_location_values == {10: 10, 11: 11}


def test_duplicate_telegram_message_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.sqlite")
    service = ProgressService(settings(tmp_path / "bot.sqlite"), store)
    now = datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    first, _ = service.submit(10, -100, 7, parse_report("1/1"), now)
    duplicate, _ = service.submit(10, -100, 7, parse_report("1/1"), now)
    assert first.status == "accepted"
    assert duplicate.status == "duplicate"
    assert service.summary(now).participants[0].report_count == 1


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
