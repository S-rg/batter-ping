from __future__ import annotations

import logging
import time
from typing import Any

import requests

from cricket_tracker.models import BattingAlert, MatchHit

logger = logging.getLogger(__name__)


class DiscordWebhookError(Exception):
    pass


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class DiscordWebhookNotifier:
    def __init__(
        self,
        webhook_url: str,
        admin_webhook_url: str | None = None,
        batting_mention: str | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self.webhook_url = webhook_url
        self.admin_webhook_url = admin_webhook_url or webhook_url
        self.batting_mention = batting_mention
        self.timeout_seconds = timeout_seconds

    def _post(self, url: str, payload: dict[str, Any]) -> None:
        attempts = 2
        for attempt in range(attempts):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout_seconds)
            except Exception as e:
                logger.error("Failed to connect to Discord webhook: %s", e)
                raise DiscordWebhookError(f"Connection error to Discord: {e}") from e

            if resp.status_code == 429:
                retry_after = 1.0
                try:
                    data = resp.json()
                    retry_after = float(data.get("retry_after", 1.0))
                except Exception:
                    header_val = resp.headers.get("Retry-After")
                    if header_val is not None:
                        try:
                            retry_after = float(header_val)
                        except ValueError:
                            pass

                logger.warning(
                    "Discord webhook 429 rate limit. Waiting %.2fs before retry", retry_after
                )
                if attempt == 0:
                    time.sleep(retry_after)
                    continue
                raise DiscordWebhookError(
                    f"Discord rate limit exceeded (429) after retry: {resp.text}"
                )

            if not (200 <= resp.status_code < 300):
                logger.error("Discord webhook responded with HTTP %d", resp.status_code)
                raise DiscordWebhookError(
                    f"Discord webhook failed with HTTP {resp.status_code}: {resp.text}"
                )

            return

    def notify_lineup(self, hit: MatchHit) -> None:
        player_lines = [f"• **{p.name}**" for p in hit.players]
        players_val = _truncate("\n".join(player_lines), 1024)

        embed = {
            "title": _truncate(f"🏏 Lineup Confirmed: {hit.title}", 256),
            "url": hit.url,
            "description": _truncate(f"**Status:** {hit.status}", 4096),
            "color": 0x3498DB,
            "fields": [
                {
                    "name": "Tracked Players in XI",
                    "value": players_val,
                    "inline": False,
                }
            ],
            "footer": {"text": "Cricket Live Player Tracker"},
        }

        payload: dict[str, Any] = {"embeds": [embed]}
        self._post(self.webhook_url, payload)

    def notify_batting(self, alert: BattingAlert) -> None:
        tracked_names = [tp.name for tp, _ in alert.batters]
        title_players = " & ".join(tracked_names)
        title = _truncate(f"🏏 {title_players} at the crease!", 256)

        fields = []
        for tp, bstate in alert.batters:
            strike_label = " ⚡ *(on strike)*" if bstate.on_strike else ""
            fields.append(
                {
                    "name": _truncate(f"{tp.name}{strike_label}", 256),
                    "value": _truncate(f"**{bstate.runs}*** ({bstate.balls} balls)", 1024),
                    "inline": True,
                }
            )

        desc = f"**{alert.title}** (Innings {alert.innings})\n**Score:** {alert.score}"
        embed = {
            "title": title,
            "url": alert.url,
            "description": _truncate(desc, 4096),
            "color": 0xE67E22,
            "fields": fields,
            "footer": {"text": "Cricket Live Player Tracker"},
        }

        payload: dict[str, Any] = {
            "embeds": [embed],
            "allowed_mentions": {"parse": ["roles", "users", "everyone"]},
        }
        if self.batting_mention:
            payload["content"] = self.batting_mention

        self._post(self.webhook_url, payload)

    def alert(self, message: str) -> None:
        embed = {
            "title": "⚠️ Cricket Tracker Health Alert",
            "description": _truncate(message, 4096),
            "color": 0xE74C3C,
            "footer": {"text": "Cricket Live Player Tracker Admin"},
        }
        payload: dict[str, Any] = {"embeds": [embed]}
        self._post(self.admin_webhook_url, payload)
