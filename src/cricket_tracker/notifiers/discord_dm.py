from __future__ import annotations

import logging
import time
from typing import Any

import requests

from cricket_tracker.models import BattingAlert, MatchHit

logger = logging.getLogger(__name__)


class DiscordDMError(Exception):
    pass


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class DiscordDMNotifier:
    def __init__(
        self,
        bot_token: str,
        user_ids: list[str | int],
        admin_user_id: str | int | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self.bot_token = bot_token.strip()
        self.user_ids = [str(uid).strip() for uid in user_ids if str(uid).strip()]
        self.admin_user_id = str(admin_user_id).strip() if admin_user_id else None
        self.timeout_seconds = timeout_seconds
        self._dm_channel_cache: dict[str, str] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
            "User-Agent": "CricketTracker (https://github.com/sideprojects/cricket-ping, 0.1.0)",
        }

    def _get_dm_channel_id(self, user_id: str) -> str:
        if user_id in self._dm_channel_cache:
            return self._dm_channel_cache[user_id]

        url = "https://discord.com/api/v10/users/@me/channels"
        payload = {"recipient_id": user_id}

        attempts = 2
        for attempt in range(attempts):
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers, timeout=self.timeout_seconds
                )
            except Exception as e:
                logger.error(
                    "Failed to connect to Discord to create DM channel for user %s: %s",
                    user_id,
                    e,
                )
                raise DiscordDMError(
                    f"Connection error creating DM channel for user {user_id}: {e}"
                ) from e

            if resp.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(resp.json().get("retry_after", 1.0))
                except Exception:
                    pass
                logger.warning(
                    "Discord 429 rate limit creating DM channel. Sleeping %.2fs", retry_after
                )
                if attempt == 0:
                    time.sleep(retry_after)
                    continue
                raise DiscordDMError(
                    f"Discord rate limit exceeded (429) creating DM channel for user {user_id}"
                )

            if not (200 <= resp.status_code < 300):
                error_msg = resp.text
                logger.error(
                    "Failed to open DM channel with Discord user %s: HTTP %d: %s",
                    user_id,
                    resp.status_code,
                    error_msg,
                )
                raise DiscordDMError(
                    f"Failed to open DM channel with user {user_id} "
                    f"(HTTP {resp.status_code}): {error_msg}"
                )

            channel_id = str(resp.json().get("id"))
            self._dm_channel_cache[user_id] = channel_id
            return channel_id

        raise DiscordDMError(f"Could not establish DM channel with user {user_id}")

    def _post_message(self, channel_id: str, payload: dict[str, Any], user_id: str) -> None:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

        attempts = 2
        for attempt in range(attempts):
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers, timeout=self.timeout_seconds
                )
            except Exception as e:
                logger.error("Failed to post Discord DM message to channel %s: %s", channel_id, e)
                raise DiscordDMError(
                    f"Connection error sending DM to channel {channel_id}: {e}"
                ) from e

            if resp.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(resp.json().get("retry_after", 1.0))
                except Exception:
                    pass
                logger.warning("Discord 429 rate limit posting DM. Sleeping %.2fs", retry_after)
                if attempt == 0:
                    time.sleep(retry_after)
                    continue
                raise DiscordDMError(
                    f"Discord rate limit exceeded (429) sending message to channel {channel_id}"
                )

            if resp.status_code == 403:
                logger.warning(
                    "Discord user %s has DMs disabled or does not share a server with the bot: %s",
                    user_id,
                    resp.text,
                )
                return

            if not (200 <= resp.status_code < 300):
                logger.error(
                    "Discord message post failed (HTTP %d): %s", resp.status_code, resp.text
                )
                raise DiscordDMError(
                    f"Discord DM post failed (HTTP {resp.status_code}): {resp.text}"
                )

            return

    def _send_to_users(self, user_ids: list[str], payload: dict[str, Any]) -> None:
        if not user_ids:
            logger.warning("No recipient user IDs configured for Discord DM notification")
            return

        errors: list[str] = []
        for uid in user_ids:
            try:
                channel_id = self._get_dm_channel_id(uid)
                self._post_message(channel_id, payload, uid)
            except Exception as e:
                errors.append(f"User {uid}: {e}")

        if errors and len(errors) == len(user_ids):
            raise DiscordDMError(f"All Discord DM deliveries failed: {'; '.join(errors)}")

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

        payload: dict[str, Any] = {
            "embeds": [embed],
            "allowed_mentions": {"parse": ["users"]},
        }
        self._send_to_users(self.user_ids, payload)

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
            "allowed_mentions": {"parse": ["users"]},
        }
        self._send_to_users(self.user_ids, payload)

    def alert(self, message: str) -> None:
        recipients = [self.admin_user_id] if self.admin_user_id else self.user_ids
        embed = {
            "title": "⚠️ Cricket Tracker Health Alert",
            "description": _truncate(message, 4096),
            "color": 0xE74C3C,
            "footer": {"text": "Cricket Live Player Tracker Admin"},
        }
        payload: dict[str, Any] = {
            "embeds": [embed],
            "allowed_mentions": {"parse": ["users"]},
        }
        self._send_to_users(recipients, payload)
