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

## Run it continuously on a Linux server (SSH and systemd)

Use these instructions if the bot should keep running after you close SSH or restart the server. They assume an Ubuntu/Debian server and that you can log in with an account that can run `sudo`.

Replace these placeholders everywhere below:

- `YOUR_SSH_USER` -- the Linux login name, for example `ubuntu`.
- `SERVER_IP` -- the server's IP address, for example `203.0.113.10`.
- `YOUR_BOT_TOKEN` -- the token that BotFather gave you. Do not share it or commit it to Git.

### 1. Copy the project to the server

On **your own computer**, open PowerShell in this project folder (`managers_bot`) and enter this command. It copies the project files to a temporary folder on the server:

```powershell
scp -r .\* YOUR_SSH_USER@SERVER_IP:/tmp/telegram-progress-bot/
```

Enter the server password if SSH asks for it. If `/tmp/telegram-progress-bot/` does not exist, first connect with SSH (the next command) and run `mkdir -p /tmp/telegram-progress-bot`, then run the `scp` command again.

### 2. Connect to the server

On your own computer, enter:

```powershell
ssh YOUR_SSH_USER@SERVER_IP
```

Everything from this point through the verification steps is entered into the **remote SSH terminal**, not PowerShell on your computer. A prompt such as `ubuntu@server:~$` means you are connected.

### 3. Install the bot and its Python requirements

Copy and run these commands one at a time in the remote SSH terminal:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
sudo useradd --system --home /opt/telegram-progress-bot --shell /usr/sbin/nologin progressbot
sudo mkdir -p /opt/telegram-progress-bot
sudo cp -a /tmp/telegram-progress-bot/. /opt/telegram-progress-bot/
sudo python3 -m venv /opt/telegram-progress-bot/.venv
sudo /opt/telegram-progress-bot/.venv/bin/pip install --upgrade pip
sudo /opt/telegram-progress-bot/.venv/bin/pip install -r /opt/telegram-progress-bot/requirements.txt
sudo chown -R progressbot:progressbot /opt/telegram-progress-bot
```

If the `useradd` command says that `progressbot` already exists, that is fine; continue with the next command.

### 4. Create the secret token file

In the remote SSH terminal, enter:

```bash
sudo nano /etc/telegram-progress-bot.env
```

Type this one line, replacing the placeholder with the real token:

```text
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
```

Save it in nano: press `Ctrl+O`, press `Enter`, then press `Ctrl+X`. Then protect the file:

```bash
sudo chmod 600 /etc/telegram-progress-bot.env
```

### 5. Create the bot configuration

Enter:

```bash
sudo nano /etc/telegram-progress-bot.toml
```

Paste the following and replace the example Telegram IDs with your actual values. `topic_id` is the numeric `message_thread_id`, not the topic's displayed name.

```toml
[telegram]
owner_user_id = 123456789
group_chat_id = -1001234567890
topic_id = 42

[reporting]
timezone = "Europe/Kyiv"
daily_reset_time = "06:00"
summary_interval_minutes = 60

[storage]
database_path = "data/progress-bot.sqlite3"
```

Again, save with `Ctrl+O`, `Enter`, `Ctrl+X`.

### 6. Register and start the service

Enter these commands in the remote SSH terminal:

```bash
sudo cp /opt/telegram-progress-bot/systemd/telegram-progress-bot.service /etc/systemd/system/telegram-progress-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-progress-bot
```

The last command both starts the bot now and configures it to start automatically after future server reboots.

### 7. Confirm that it is running

Enter:

```bash
sudo systemctl status telegram-progress-bot --no-pager
```

Look for `Active: active (running)`. You can also use this short check:

```bash
sudo systemctl is-active telegram-progress-bot
```

It must print exactly `active`. To see the bot's most recent messages or an error, enter:

```bash
sudo journalctl -u telegram-progress-bot -n 50 --no-pager
```

To watch logs live while you send the bot a Telegram message, enter `sudo journalctl -u telegram-progress-bot -f`; press `Ctrl+C` to stop watching. Finally, send `/status` to the bot from the configured owner's private Telegram chat. A response confirms that the running service can reach Telegram and that the configuration is usable.

### Updating or restarting later

After copying updated project files to `/tmp/telegram-progress-bot/`, repeat the copy, dependency-install, ownership, service-copy, and reload commands from steps 3 and 6, then enter:

```bash
sudo systemctl restart telegram-progress-bot
sudo systemctl is-active telegram-progress-bot
```

If it does not print `active`, run `sudo journalctl -u telegram-progress-bot -n 50 --no-pager` and use the error shown there to diagnose the problem.

Back up the SQLite database regularly with SQLite's online backup command or a filesystem snapshot. Do not copy only the `.sqlite3` file while the service is running without accounting for WAL files.

## Owner commands

- `/adduser <telegram_id> [name]` / `/removeuser <telegram_id>` -- for example, `/adduser 123456789 Alice Smith`; summaries then show `Alice Smith (123456789)`.
- `/users`
- `/target <integer>` or `/target`
- `/status`
- `/help`

Employee reports are `n/m`; `*n/m` corrects that user's latest accepted report in the current business day. Invalid text, edits, deleted messages, unmonitored users, and messages outside the configured topic are ignored.

Rejected text reports are written to the service log with a reason, chat/topic/message/user IDs, and the message text (long text is truncated). On the server, view them with:

```bash
sudo journalctl -u telegram-progress-bot -f
```

## Translating bot messages

All text sent by the bot is loaded from a JSON translation file. English is the default and is defined in `progress_bot/translations/en.json`. Do not change the JSON keys on the left; translate only the text on the right and keep placeholders such as `{name}`, `{count}`, and `{target}` unchanged.

To add a language, copy `en.json` to a two-letter language filename, for example `uk.json`, translate its values, and set the language in the `[reporting]` section of `config.toml`:

```toml
language = "uk"
```

On the server, the file must be at `/opt/telegram-progress-bot/progress_bot/translations/uk.json`. Restart the service after changing the configuration or a translation file:

```bash
sudo systemctl restart telegram-progress-bot
```

If the selected translation file, key, or value is missing, the bot uses the English text instead.
