from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    token: str
    owner_user_id: int
    group_chat_id: int
    topic_id: int
    timezone: ZoneInfo
    daily_reset_time: time
    summary_interval_minutes: int
    database_path: Path


def load_settings(path: str | Path, environ: dict[str, str] | None = None) -> Settings:
    environment = os.environ if environ is None else environ
    token = environment.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN must be set")
    with Path(path).open("rb") as config_file:
        raw = tomllib.load(config_file)
    try:
        telegram = raw["telegram"]
        reporting = raw["reporting"]
        storage = raw["storage"]
        reset = time.fromisoformat(reporting["daily_reset_time"])
        if reset.tzinfo is not None:
            raise ValueError("daily_reset_time must not contain a timezone")
        interval = int(reporting.get("summary_interval_minutes", 60))
        if interval <= 0 or interval > 1440:
            raise ValueError("summary_interval_minutes must be between 1 and 1440")
        return Settings(
            token=token,
            owner_user_id=int(telegram["owner_user_id"]),
            group_chat_id=int(telegram["group_chat_id"]),
            topic_id=int(telegram["topic_id"]),
            timezone=ZoneInfo(reporting["timezone"]),
            daily_reset_time=reset,
            summary_interval_minutes=interval,
            database_path=Path(storage["database_path"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid configuration in {path}: {error}") from error

