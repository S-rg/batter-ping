from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cricket_tracker.models import TrackedPlayer

_ENV_VAR_PATTERN = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class NotifyConfig:
    lineup: bool = True
    batting: bool = True


@dataclass(frozen=True)
class NotifierConfig:
    type: str = "console"
    bot_token: str | None = None
    user_ids: list[str] = field(default_factory=list)
    admin_user_id: str | None = None
    webhook_url: str | None = None
    admin_webhook_url: str | None = None
    batting_mention: str | None = None


@dataclass(frozen=True)
class AppConfig:
    state_file: Path
    max_workers: int
    squads_recheck_seconds: int
    xi_recheck_seconds: int
    evict_after_seconds: int
    alert_after_failed_runs: int
    request_timeout_seconds: int
    notify: NotifyConfig
    notifier: NotifierConfig
    players: list[TrackedPlayer] = field(default_factory=list)
    browser_max_tabs: int = 4
    fetch_budget_seconds: int = 30
    browser_javascript: bool = False


def _expand_env_vars(val: Any) -> Any:
    if isinstance(val, str):

        def repl(match: re.Match) -> str:
            if match.group(1):
                var_name = match.group(1)
                default_val = match.group(2) if match.group(2) is not None else ""
                return os.environ.get(var_name, default_val)
            if match.group(3):
                var_name = match.group(3)
                return os.environ.get(var_name, "")
            return ""

        return _ENV_VAR_PATTERN.sub(repl, val)
    if isinstance(val, dict):
        return {k: _expand_env_vars(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_expand_env_vars(item) for item in val]
    return val


def load_config(config_path: str | Path, validate_notifier: bool = True) -> AppConfig:
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except Exception as e:
        raise ConfigError(f"Error parsing YAML from {path}: {e}") from e

    if not isinstance(raw_data, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw_data).__name__}")

    data = _expand_env_vars(raw_data)

    raw_state_file = data.get("state_file", "./state.json")
    if not isinstance(raw_state_file, str) or not raw_state_file.strip():
        raise ConfigError("state_file must be a non-empty string path")
    state_file = Path(raw_state_file)
    if not state_file.is_absolute():
        state_file = (path.parent / state_file).resolve()

    def get_pos_int(key: str, default: int) -> int:
        val = data.get(key, default)
        if val == "" or val is None:
            val = default
        elif isinstance(val, str) and val.strip().isdigit():
            val = int(val.strip())
        if not isinstance(val, int) or val <= 0:
            raise ConfigError(f"{key} must be a positive integer, got {val!r}")
        return val

    max_workers = get_pos_int("max_workers", 8)
    squads_recheck_seconds = get_pos_int("squads_recheck_seconds", 600)
    xi_recheck_seconds = get_pos_int("xi_recheck_seconds", 300)
    evict_after_seconds = get_pos_int("evict_after_seconds", 21600)
    alert_after_failed_runs = get_pos_int("alert_after_failed_runs", 3)
    request_timeout_seconds = get_pos_int("request_timeout_seconds", 10)
    browser_max_tabs = get_pos_int("browser_max_tabs", 4)
    fetch_budget_seconds = get_pos_int("fetch_budget_seconds", 30)

    raw_js = data.get("browser_javascript", False)
    if isinstance(raw_js, str):
        if raw_js.strip().lower() not in ("true", "false", "yes", "no", "1", "0", ""):
            raise ConfigError(f"browser_javascript must be true or false, got {raw_js!r}")
        raw_js = raw_js.strip().lower() in ("true", "yes", "1")
    browser_javascript = bool(raw_js)

    raw_notify = data.get("notify", {})
    if not isinstance(raw_notify, dict):
        raise ConfigError("notify section must be a mapping")
    notify = NotifyConfig(
        lineup=bool(raw_notify.get("lineup", True)),
        batting=bool(raw_notify.get("batting", True)),
    )

    raw_notifier = data.get("notifier", {})
    if not isinstance(raw_notifier, dict):
        raise ConfigError("notifier section must be a mapping")
    notifier_type = raw_notifier.get("type", "console")
    allowed_types = ("console", "discord_dm", "discord", "discord_webhook")
    if notifier_type not in allowed_types:
        allowed_str = ", ".join(repr(t) for t in allowed_types)
        raise ConfigError(f"Unknown notifier type: {notifier_type!r}. Allowed: {allowed_str}")

    bot_token = raw_notifier.get("bot_token")
    if bot_token is not None:
        bot_token = str(bot_token).strip() or None

    raw_user_ids = raw_notifier.get("user_ids", [])
    if isinstance(raw_user_ids, (str, int)):
        raw_user_ids = [raw_user_ids]
    if not isinstance(raw_user_ids, list):
        raise ConfigError("notifier.user_ids must be a list of user IDs")
    user_ids = [str(u).strip() for u in raw_user_ids if str(u).strip()]

    admin_user_id = raw_notifier.get("admin_user_id")
    if admin_user_id is not None:
        admin_user_id = str(admin_user_id).strip() or None

    webhook_url = raw_notifier.get("webhook_url")
    if webhook_url is not None:
        webhook_url = str(webhook_url).strip() or None

    admin_webhook_url = raw_notifier.get("admin_webhook_url")
    if admin_webhook_url is not None:
        admin_webhook_url = str(admin_webhook_url).strip() or None

    batting_mention = raw_notifier.get("batting_mention")
    if batting_mention is not None:
        batting_mention = str(batting_mention).strip() or None

    if validate_notifier:
        if notifier_type == "discord_webhook" and not webhook_url:
            raise ConfigError(
                "notifier.webhook_url is required when notifier.type is 'discord_webhook'"
            )
        if notifier_type in ("discord_dm", "discord"):
            if not bot_token:
                raise ConfigError(
                    "notifier.bot_token is required when notifier.type is 'discord_dm'"
                )
            if not user_ids:
                raise ConfigError(
                    "notifier.user_ids is required when notifier.type is 'discord_dm'"
                )

    notifier = NotifierConfig(
        type=notifier_type,
        bot_token=bot_token,
        user_ids=user_ids,
        admin_user_id=admin_user_id,
        webhook_url=webhook_url,
        admin_webhook_url=admin_webhook_url,
        batting_mention=batting_mention,
    )

    raw_players = data.get("players", [])
    if not isinstance(raw_players, list):
        raise ConfigError("players section must be a list")

    players: list[TrackedPlayer] = []
    seen_ids: set[int] = set()

    for idx, p in enumerate(raw_players):
        if not isinstance(p, dict):
            raise ConfigError(f"Player item at index {idx} must be a dict")
        pid = p.get("id")
        pname = p.get("name")
        if not isinstance(pid, int) or pid <= 0:
            raise ConfigError(f"Player id at index {idx} must be a positive integer, got {pid!r}")
        if not isinstance(pname, str) or not pname.strip():
            raise ConfigError(
                f"Player name at index {idx} must be a non-empty string, got {pname!r}"
            )
        if pid in seen_ids:
            raise ConfigError(f"Duplicate player ID {pid} in config")
        seen_ids.add(pid)
        players.append(TrackedPlayer(id=pid, name=pname.strip()))

    return AppConfig(
        state_file=state_file,
        max_workers=max_workers,
        squads_recheck_seconds=squads_recheck_seconds,
        xi_recheck_seconds=xi_recheck_seconds,
        evict_after_seconds=evict_after_seconds,
        alert_after_failed_runs=alert_after_failed_runs,
        request_timeout_seconds=request_timeout_seconds,
        notify=notify,
        notifier=notifier,
        players=players,
        browser_max_tabs=browser_max_tabs,
        fetch_budget_seconds=fetch_budget_seconds,
        browser_javascript=browser_javascript,
    )
