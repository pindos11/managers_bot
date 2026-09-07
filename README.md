# Telegram Progress-Reporting Bot

The bot accepts reports only from configured monitored users in one Telegram forum topic. The owner controls it through a private chat.

## Setup

1. Create a bot with BotFather. Disable the bot's privacy mode (or otherwise permit it to receive ordinary group messages), add it to the target supergroup, give it access to topic messages, and start a private chat with it as the configured owner.
2. Copy `config.example.toml` to a private `config.toml` and set the IDs. `topic_id` is Telegram's `message_thread_id`, not the visible topic name.
3. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

4. Set `TELEGRAM_BOT_TOKEN` in the environment, then run:

   ```bash
   .venv/bin/python -m progress_bot --config config.toml
   ```

For systemd, install `systemd/telegram-progress-bot.service`, place TOML configuration at `/etc/telegram-progress-bot.toml`, place `TELEGRAM_BOT_TOKEN=...` in `/etc/telegram-progress-bot.env` (mode `0600`), and run `systemctl enable --now telegram-progress-bot`.

Back up the SQLite database regularly with SQLite's online backup command or a filesystem snapshot. Do not copy only the `.sqlite3` file while the service is running without accounting for WAL files.

## Owner commands

- `/adduser <telegram_id>` / `/removeuser <telegram_id>`
- `/users`
- `/target <integer>` or `/target`
- `/status`
- `/help`

Employee reports are `n/m`; `*n/m` corrects that user's latest accepted report in the current business day. Invalid text, edits, deleted messages, unmonitored users, and messages outside the configured topic are ignored.
