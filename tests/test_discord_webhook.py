from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cricket_tracker.models import BatterState, BattingAlert, MatchHit, TrackedPlayer
from cricket_tracker.notifiers.discord_webhook import (
    DiscordWebhookError,
    DiscordWebhookNotifier,
)


@patch("requests.post")
def test_discord_lineup_embed(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp

    notifier = DiscordWebhookNotifier(
        webhook_url="https://discord.com/api/webhooks/test/lineup",
    )

    hit = MatchHit(
        match_id=10,
        title="India v Australia",
        status="Match starting soon",
        url="https://cricinfo.com/10",
        players=(TrackedPlayer(253802, "Virat Kohli"),),
    )
    notifier.notify_lineup(hit)

    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert args[0] == "https://discord.com/api/webhooks/test/lineup"
    payload = kwargs["json"]
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Lineup Confirmed" in embed["title"]
    assert embed["url"] == "https://cricinfo.com/10"
    assert "Virat Kohli" in embed["fields"][0]["value"]


@patch("requests.post")
def test_discord_batting_embed_with_mention(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp

    notifier = DiscordWebhookNotifier(
        webhook_url="https://discord.com/api/webhooks/test/batting",
        batting_mention="<@&123456>",
    )

    tp = TrackedPlayer(253802, "Virat Kohli")
    bstate = BatterState(player_id=253802, name="Virat Kohli", runs=42, balls=28, on_strike=True)
    alert = BattingAlert(
        match_id=10,
        title="India v Australia",
        url="https://cricinfo.com/10",
        innings=1,
        batting_team="IND",
        score="IND 88/2 (11.4)",
        batters=((tp, bstate),),
    )
    notifier.notify_batting(alert)

    assert mock_post.called
    kwargs = mock_post.call_args[1]
    payload = kwargs["json"]
    assert payload["content"] == "<@&123456>"
    embed = payload["embeds"][0]
    assert "Virat Kohli at the crease" in embed["title"]
    assert "42*" in embed["fields"][0]["value"]
    assert "(on strike)" in embed["fields"][0]["name"]


@patch("time.sleep")
@patch("requests.post")
def test_discord_429_retry(mock_post: MagicMock, mock_sleep: MagicMock) -> None:
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.json.return_value = {"retry_after": 0.5}

    resp_200 = MagicMock()
    resp_200.status_code = 200

    mock_post.side_effect = [resp_429, resp_200]

    notifier = DiscordWebhookNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
    )
    hit = MatchHit(
        match_id=1,
        title="T",
        status="S",
        url="U",
        players=(TrackedPlayer(1, "P"),),
    )
    notifier.notify_lineup(hit)

    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(0.5)


@patch("requests.post")
def test_discord_server_error_raises(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_post.return_value = mock_resp

    notifier = DiscordWebhookNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
    )
    hit = MatchHit(
        match_id=1,
        title="T",
        status="S",
        url="U",
        players=(TrackedPlayer(1, "P"),),
    )
    with pytest.raises(DiscordWebhookError) as exc_info:
        notifier.notify_lineup(hit)
    assert "HTTP 500" in str(exc_info.value)
