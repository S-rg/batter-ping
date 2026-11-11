# Cricket Live Player Tracker

A lightweight Python CLI, run from cron, that watches live cricket on ESPNcricinfo and messages
you (Discord DM, Discord webhook, or the console) when players you follow are named in a playing
XI and when they walk out to bat.

Match data comes from [python-espncricinfo](https://github.com/outside-edge/python-espncricinfo)
by Derek Willis and Gaurav Sood. This repo ships a lightly modified copy of it in
`vendor/python-espncricinfo` that uses far less RAM. See [Credits](#credits-and-license) for
what changed.

---

## Requirements

- **Python 3.12 or newer** (required by python-espncricinfo)
- **Linux or macOS** (the run lock uses `fcntl.flock`)
- **Playwright's WebKit browser.** ESPNcricinfo's CDN blocks plain HTTP clients, so match pages
  are loaded in a headless browser. Installing its system libraries on Linux needs `sudo`.
- **About 350 MB of free RAM** while a run is fetching (one browser, one tab). Busy days may open
  a few more tabs, at about 100 MB each (see [Memory use](#memory-use)).

---

## Installation

### 1. Get the code

```bash
git clone https://github.com/S-rg/batter-ping.git
cd batter-ping
```

### 2. Create a virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3. Install the vendored library, then the tracker

```bash
pip install -e vendor/python-espncricinfo
pip install -e .
```

The order matters. The tracker depends on `python-espncricinfo`. If the vendored copy is not
installed first, pip downloads the original release from PyPI instead, and you lose the memory
savings. To check which copy is in use:

```bash
python -c "import espncricinfo, os; print(os.path.dirname(espncricinfo.__file__))"
```

The printed path should end in `vendor/python-espncricinfo/espncricinfo`.

For development (pytest and ruff), use `pip install -e ".[dev]"` in place of `pip install -e .`.

### 4. Install the WebKit browser

```bash
playwright install --with-deps webkit
```

`--with-deps` also installs the system libraries WebKit needs, and asks for `sudo` on Linux. If
they are already installed, `playwright install webkit` is enough.

### 5. Check it works

```bash
cricket-tracker --help
cricket-tracker --config config.yaml --dry-run --verbose
```

A dry run fetches the live match list from ESPNcricinfo and prints what it would send. It needs
no Discord setup, saves no state and sends nothing. A line such as
`live=0 lineups=0 ... errors=0` means everything is working. `live=0` just means no matches are
in progress at the moment.

---

## Configuration

Edit `config.yaml`, or copy it and point `--config` at your copy:

```yaml
state_file: ./state.json
max_workers: 3
squads_recheck_seconds: 600
xi_recheck_seconds: 300
evict_after_seconds: 86400
alert_after_failed_runs: 3
request_timeout_seconds: 10
browser_max_tabs: 4         # upper limit on browser tabs (each ~100 MB)
fetch_budget_seconds: 30    # use the fewest tabs that finish each batch in this time
browser_javascript: false   # page scripts aren't needed for the data; off saves RAM

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

You can use environment variables anywhere in the file, as `${VAR}` or with a default as
`${VAR:-default}`.

| Key | Default | Meaning |
|---|---|---|
| `state_file` | (required) | Where run state is kept. Relative paths are relative to the config file. |
| `max_workers` | `8` | Most matches fetched in parallel. In practice the tab planning below sets the limit. |
| `squads_recheck_seconds` | `600` | How often to recheck a match whose squad is known but whose XI isn't announced yet. |
| `xi_recheck_seconds` | `300` | How often to recheck a match once the XI is known. |
| `evict_after_seconds` | `21600` | Forget a match this long after it was last seen live. |
| `alert_after_failed_runs` | `3` | Send an admin alert after this many runs in a row fail to load the live match list. |
| `request_timeout_seconds` | `10` | Timeout for Discord API requests. |
| `browser_max_tabs` | `4` | The most browser tabs open at once. |
| `fetch_budget_seconds` | `30` | Before each batch, open the fewest tabs expected to finish it in this time. |
| `browser_javascript` | `false` | Run ESPNcricinfo's page scripts. Not needed, because the data is in the page HTML. |
| `notify.lineup` | `true` | Alert when a tracked player is named in a playing XI. |
| `notify.batting` | `true` | Alert when a tracked player comes in to bat, once per innings. |
| `notifier.type` | `console` | `console`, `discord_dm`, or `discord_webhook`. |
| `notifier.bot_token`, `notifier.user_ids` | | Required for `discord_dm`. `admin_user_id` gets health alerts. |
| `notifier.webhook_url` | | Required for `discord_webhook`. Optional: `admin_webhook_url`, and `batting_mention` (e.g. `<@&role_id>`). |
| `players` | | The players to track: `{ id, name }` entries. |

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

## Cron Deployment

Add a crontab entry to run the tracker every 5 minutes:

```cron
*/5 * * * * cd /opt/batter-ping && DISCORD_BOT_TOKEN="..." DISCORD_USER_ID="..." /opt/batter-ping/.venv/bin/cricket-tracker --config config.yaml >> /var/log/cricket_tracker.log 2>&1
```

## Logrotate Configuration

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

## Memory use

Each run starts **one** headless WebKit browser and reuses its tabs:

- Each fetch borrows an idle tab, loads the page and reads it. The tab is then cleared
  (`about:blank`) and returned to the pool.
- Before each batch of fetches, the tracker picks the fewest tabs expected to finish the batch
  within `fetch_budget_seconds`. The estimate uses how long fetches have actually taken during
  the run, and is capped at `browser_max_tabs`. One or two live matches use a single tab.
- Images, fonts, stylesheets and media are not downloaded, and page JavaScript is off by default.
- The browser closes at the end of every run, so nothing stays in memory between cron runs.

If memory is tight, raise `fetch_budget_seconds` (e.g. to `120`) or set `browser_max_tabs: 1`.
Runs get slower but stay at one tab. If you need runs to finish faster, do the opposite.

---

## Development & Testing

Install with the dev extras (`pip install -e vendor/python-espncricinfo -e ".[dev]"`), then:

```bash
pytest tests/ -v
```

```bash
ruff check .
ruff format --check .
```

The vendored library has its own test suite:

```bash
cd vendor/python-espncricinfo && pytest -o addopts="" -q
```

---

## Credits and License

### python-espncricinfo

All ESPNcricinfo scraping and parsing is done by
**[python-espncricinfo](https://github.com/outside-edge/python-espncricinfo)**, written and
maintained by **Derek Willis** and **Gaurav Sood**
([PyPI](https://pypi.org/project/python-espncricinfo/),
[docs](https://outside-edge.github.io/python-espncricinfo/)). It is released under the MIT
License, Copyright (c) 2015 Derek Willis. The original license is kept unchanged at
[`vendor/python-espncricinfo/LICENSE.txt`](vendor/python-espncricinfo/LICENSE.txt), and the
upstream citation metadata is in
[`CITATION.cff`](vendor/python-espncricinfo/CITATION.cff). If you use that library in your own
work, please cite it as that file asks.

`vendor/python-espncricinfo` is a copy of upstream version **1.1.0** (commit
[`f88219a`](https://github.com/outside-edge/python-espncricinfo/commit/f88219a47a8de29f4a41ea8fe6f5d1d578a922cf),
taken 2026-11-11), with the changes below. They are this project's changes, not the upstream
authors' work, so please don't report problems with them upstream.

**Why:** upstream starts a new Playwright WebKit browser for every page fetch. This tracker
fetches several matches in parallel, so it ended up running several full browsers at once, and
peaked at about 3.5 GB of RAM fetching 6 matches.

**What changed:**

- **New `espncricinfo/_browser.py`: one shared browser per process.** It is started on first
  use, runs on its own background thread and event loop, is relaunched if it crashes, and is
  closed when Python exits.
- **Tabs are reused, not created per fetch.** Each tab is cleared between fetches, and replaced
  after 50 fetches or after any failed fetch.
- **The tab limit can change during a run, and a planner sizes it.** `plan_pages()` returns the
  fewest tabs that should finish a batch in a time budget, based on a running average of real
  fetch times. Lowering the limit closes surplus idle tabs straight away.
- **Images, fonts, stylesheets and media are blocked.** Page JavaScript can be turned off; it is
  on by default in the library and off by default in this tracker.
- **`espncricinfo/match.py`:** `_playwright_fetch()` and `_async_playwright_fetch()` now use the
  shared browser. Their signatures are unchanged, and they still work from inside a running event
  loop (e.g. Jupyter).
- **New public functions in `espncricinfo`:** `configure_browser()`, `plan_pages()` and
  `close_browser()`.
- **New tests** in `tests/test_browser.py`. The rest of the library is unchanged, and all
  upstream tests still pass.

The full list is in [`vendor/python-espncricinfo/CHANGELOG.md`](vendor/python-espncricinfo/CHANGELOG.md),
under "Vendored modifications".

### ESPNcricinfo

Neither this project nor python-espncricinfo is affiliated with ESPNcricinfo or ESPN. Match data
belongs to ESPNcricinfo. Use it for personal, non-commercial purposes, and keep polling to a
reasonable rate (the default every-5-minutes cron is gentle).
