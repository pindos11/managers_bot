from pathlib import Path

import pytest

from progress_bot.config import load_settings


def test_load_settings(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("""[telegram]\nowner_user_id=1\ngroup_chat_id=-2\ntopic_id=3\n[reporting]\ntimezone='Europe/Kyiv'\ndaily_reset_time='06:00'\n[storage]\ndatabase_path='bot.sqlite'\n""")
    settings = load_settings(config, {"TELEGRAM_BOT_TOKEN": "secret"})
    assert settings.owner_user_id == 1 and settings.summary_interval_minutes == 60 and settings.language == "en"


def test_token_is_required(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[telegram]\n")
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_settings(config, {})
