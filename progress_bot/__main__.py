from __future__ import annotations

import argparse
from pathlib import Path

from .bot import TelegramProgressBot
from .config import load_settings
from .logging_utils import configure_logging
from .storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram progress-reporting bot")
    parser.add_argument("--config", default="config.toml", help="Path to TOML configuration")
    args = parser.parse_args()
    configure_logging()
    settings = load_settings(Path(args.config))
    store = Store(settings.database_path)
    try:
        TelegramProgressBot(settings, store).application().run_polling(drop_pending_updates=False, allowed_updates=["message"])
    finally:
        store.close()


if __name__ == "__main__":
    main()
