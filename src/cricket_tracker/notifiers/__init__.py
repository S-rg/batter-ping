from __future__ import annotations

from cricket_tracker.config import AppConfig
from cricket_tracker.notifiers.base import Notifier
from cricket_tracker.notifiers.console import ConsoleNotifier
from cricket_tracker.notifiers.discord_dm import DiscordDMNotifier
from cricket_tracker.notifiers.discord_webhook import DiscordWebhookNotifier


def build_notifier(config: AppConfig, dry_run: bool = False) -> Notifier:
    if dry_run or config.notifier.type == "console":
        return ConsoleNotifier()

    if config.notifier.type in ("discord_dm", "discord"):
        if not config.notifier.bot_token:
            raise ValueError("notifier.bot_token is required when notifier.type is 'discord_dm'")
        if not config.notifier.user_ids:
            raise ValueError("notifier.user_ids is required when notifier.type is 'discord_dm'")
        return DiscordDMNotifier(
            bot_token=config.notifier.bot_token,
            user_ids=config.notifier.user_ids,
            admin_user_id=config.notifier.admin_user_id,
            timeout_seconds=config.request_timeout_seconds,
        )

    if config.notifier.type == "discord_webhook":
        if not config.notifier.webhook_url:
            raise ValueError("Webhook URL required for discord_webhook notifier")
        return DiscordWebhookNotifier(
            webhook_url=config.notifier.webhook_url,
            admin_webhook_url=config.notifier.admin_webhook_url,
            batting_mention=config.notifier.batting_mention,
            timeout_seconds=config.request_timeout_seconds,
        )

    raise ValueError(f"Unsupported notifier type: {config.notifier.type}")
