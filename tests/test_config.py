from __future__ import annotations

from pathlib import Path

import pytest

from cricket_tracker.config import ConfigError, load_config


def test_load_config_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DISCORD_URL", "https://discord.com/api/webhooks/test")
    monkeypatch.setenv("TEST_ROLE", "<@&999>")

    config_content = """
state_file: ./custom_state.json
max_workers: 4
squads_recheck_seconds: 500
xi_recheck_seconds: 250
evict_after_seconds: 10000
alert_after_failed_runs: 5
request_timeout_seconds: 15

notify:
  lineup: true
  batting: false

notifier:
  type: discord_webhook
  webhook_url: ${TEST_DISCORD_URL}
  batting_mention: ${TEST_ROLE}

players:
  - id: 253802
    name: Virat Kohli
  - id: 625383
    name: Jasprit Bumrah
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_content, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert cfg.state_file == tmp_path / "custom_state.json"
    assert cfg.max_workers == 4
    assert cfg.notify.lineup is True
    assert cfg.notify.batting is False
    assert cfg.notifier.webhook_url == "https://discord.com/api/webhooks/test"
    assert cfg.notifier.batting_mention == "<@&999>"
    assert len(cfg.players) == 2
    assert cfg.players[0].id == 253802


def test_config_duplicate_players_fails(tmp_path: Path) -> None:
    config_content = """
notifier:
  type: console
players:
  - id: 100
    name: Player One
  - id: 100
    name: Duplicate Player
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_content, encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_file)
    assert "Duplicate player ID 100" in str(exc_info.value)


def test_config_missing_webhook_fails(tmp_path: Path) -> None:
    config_content = """
notifier:
  type: discord_webhook
players: []
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_content, encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_file)
    assert "notifier.webhook_url is required" in str(exc_info.value)


def test_load_repo_default_config_for_dry_run() -> None:
    repo_cfg = Path(__file__).parent.parent / "config.yaml"
    cfg = load_config(repo_cfg, validate_notifier=False)
    assert cfg.max_workers == 3
    assert cfg.evict_after_seconds == 86400
    assert cfg.notifier.type == "discord_dm"
    assert len(cfg.players) == 2


def test_env_var_default_and_integer_parsing(tmp_path: Path) -> None:
    config_content = """
max_workers: ${MAX_WORKERS:-6}
request_timeout_seconds: $TIMEOUT_SEC
notifier:
  type: discord_dm
  bot_token: ${BOT_TOKEN:-default_bot_token}
  user_ids:
    - ${USER_ID:-12345678}
  admin_user_id: ${ADMIN_ID:-87654321}
players: []
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_content, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert cfg.max_workers == 6
    assert cfg.request_timeout_seconds == 10
    assert cfg.notifier.bot_token == "default_bot_token"
    assert cfg.notifier.user_ids == ["12345678"]
    assert cfg.notifier.admin_user_id == "87654321"


def test_config_missing_discord_dm_credentials_fails(tmp_path: Path) -> None:
    config_content = """
notifier:
  type: discord_dm
  bot_token: ""
  user_ids: []
players: []
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_content, encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_file, validate_notifier=True)
    assert "notifier.bot_token is required" in str(exc_info.value)


def _minimal_config(tmp_path: Path, extra: str = "") -> Path:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"state_file: ./s.json\n{extra}\nnotifier:\n  type: console\n"
        "players:\n  - { id: 1, name: A }\n",
        encoding="utf-8",
    )
    return cfg_file


def test_browser_defaults_favor_low_ram(tmp_path: Path) -> None:
    cfg = load_config(_minimal_config(tmp_path))
    assert cfg.browser_max_tabs == 4
    assert cfg.fetch_budget_seconds == 30
    assert cfg.browser_javascript is False


def test_browser_settings_parsed(tmp_path: Path) -> None:
    cfg = load_config(_minimal_config(tmp_path, "browser_max_tabs: 3\nbrowser_javascript: 'true'"))
    assert cfg.browser_max_tabs == 3
    assert cfg.browser_javascript is True


def test_browser_tabs_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_minimal_config(tmp_path, "browser_max_tabs: 0"))
