# Cricket Live Player Tracker

A lightweight, robust Python CLI executed via cron to track specific cricket players to ensure you can always catch when they are batting.

Built with `python-espncricinfo`.

---

## Installation

### 1. Requirements

- Python 3.10 or higher
- Linux / macOS (for `fcntl.flock` support)
- Playwright WebKit browser (used by `python-espncricinfo` to bypass CDN blocks)

### 2. Setup Virtual Environment

```bash
git clone https://github.com/your-username/cricket-ping.git
cd cricket-ping

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install --with-deps webkit
```

---

## Configuration

Copy `config.yaml` or edit the default:

```yaml
state_file: ./state.json
max_workers: 3
squads_recheck_seconds: 600
xi_recheck_seconds: 300
evict_after_seconds: 86400
alert_after_failed_runs: 3
request_timeout_seconds: 10

notify:
  lineup: true
  batting: true

notifier:
  type: discord_dm
  bot_token: ${DISCORD_BOT_TOKEN}
  user_ids:
    - ${DISCORD_USER_ID}
  admin_user_id: ${DISCORD_ADMIN_USER_ID}

players:
  - { id: 253802, name: Virat Kohli }
  - { id: 625383, name: Jasprit Bumrah }
```

Environment variables and defaults like `${DISCORD_BOT_TOKEN}` or `${VAR:-default}` can be used anywhere in the config file.

### Discord Bot Setup (for Direct Messages)

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Go to **Bot** in the left sidebar:
   - Click **Reset Token** to generate a bot token, then export it: `export DISCORD_BOT_TOKEN="your_bot_token"`.
3. Invite the bot to your server:
   - In the left sidebar, click **OAuth2** → **URL Generator**.
   - Check the **`bot`** scope.
   - Under Bot Permissions, select **Send Messages** and **Embed Links**.
   - Copy the generated URL at the bottom, open it in your browser, and add the bot to a Discord server you belong to *(Discord requires a bot to share at least one mutual server with users it DMs)*.
4. Verify your DM privacy settings in Discord:
   - Go to **User Settings** (cog icon) → **Privacy & Safety** → ensure **Allow direct messages from server members** is enabled.
5. Get your Discord User ID:
   - Go to **User Settings** → **Advanced** → toggle on **Developer Mode**.
   - Right-click your profile picture in Discord and click **Copy User ID** (`export DISCORD_USER_ID="your_user_id"`).

### Finding Player IDs

Player IDs are the numeric object IDs in ESPNcricinfo profile URLs (e.g., `https://www.espncricinfo.com/player/virat-kohli-253802` -> `253802`).

You can use the installed CLI command or helper script to resolve IDs into formatted YAML:

```bash
cricket-resolve-players 253802 625383
# or:
python scripts/resolve_players.py 253802 625383
```

Output:
```yaml
players:
  - { id: 253802, name: Virat Kohli }
  - { id: 625383, name: Jasprit Bumrah }
```

---

## Usage

### 1. Test Discord DM delivery (Quick Test)

Verify that your bot token and user ID can send DMs immediately:

```bash
python -c "
from cricket_tracker.notifiers.discord_dm import DiscordDMNotifier
import os
notifier = DiscordDMNotifier(bot_token=os.environ['DISCORD_BOT_TOKEN'], user_ids=[os.environ['DISCORD_USER_ID']])
notifier.alert('🏏 Test message from Cricket Tracker!')
print('DM sent successfully!')
"
```

### 2. Run a dry run

Run with `--dry-run` to print live match checks and potential alerts to the console without saving state or sending Discord notifications:

```bash
cricket-tracker --config config.yaml --dry-run --verbose
```

### 3. Run a live check

Run once manually with full live fetching and notifications:

```bash
cricket-tracker --config config.yaml --verbose
```

### 4. Clear state

If you ever need to safely reset the state cache:

```bash
cricket-tracker --config config.yaml --reset-state
```

---

## Cron Deployment

Add a crontab entry to run the tracker every 5 minutes:

```cron
*/5 * * * * cd /opt/cricket-ping && DISCORD_BOT_TOKEN="..." DISCORD_USER_ID="..." /opt/cricket-ping/.venv/bin/cricket-tracker --config config.yaml >> /var/log/cricket_tracker.log 2>&1
```

### Logrotate Configuration

Create `/etc/logrotate.d/cricket_tracker`:

```text
/var/log/cricket_tracker.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 root adm
}
```

---

## Development & Testing

Run unit tests:
```bash
pytest tests/ -v
```

Run code formatting and linting:
```bash
ruff check .
ruff format --check .
```
