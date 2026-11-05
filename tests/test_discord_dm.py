from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cricket_tracker.models import BatterState, BattingAlert, MatchHit, TrackedPlayer
from cricket_tracker.notifiers.discord_dm import DiscordDMError, DiscordDMNotifier


@patch("requests.post")
def test_discord_dm_channel_cached_and_message_sent(mock_post: MagicMock) -> None:
    resp_dm = MagicMock()
    resp_dm.status_code = 200
    resp_dm.json.return_value = {"id": "dm_123"}

    resp_msg = MagicMock()
    resp_msg.status_code = 200

    mock_post.side_effect = [resp_dm, resp_msg, resp_msg]

    notifier = DiscordDMNotifier(
        bot_token="test_token_xyz",
        user_ids=["987654321"],
    )

    hit = MatchHit(
        match_id=1,
        title="India v Australia 1st T20I",
        status="Match starting soon",
        url="https://cricinfo.com/1",
        players=(TrackedPlayer(253802, "Virat Kohli"),),
    )
    notifier.notify_lineup(hit)

    assert mock_post.call_count == 2
    dm_call_args, dm_call_kwargs = mock_post.call_args_list[0]
    assert dm_call_args[0] == "https://discord.com/api/v10/users/@me/channels"
    assert dm_call_kwargs["headers"]["Authorization"] == "Bot test_token_xyz"
    assert dm_call_kwargs["json"] == {"recipient_id": "987654321"}

    msg_call_args, msg_call_kwargs = mock_post.call_args_list[1]
    assert msg_call_args[0] == "https://discord.com/api/v10/channels/dm_123/messages"
    payload = msg_call_kwargs["json"]
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Virat Kohli" in embed["fields"][0]["value"]

    notifier.notify_lineup(hit)
    assert mock_post.call_count == 3


@patch("requests.post")
def test_discord_dm_batting_alert(mock_post: MagicMock) -> None:
    resp_dm = MagicMock()
    resp_dm.status_code = 200
    resp_dm.json.return_value = {"id": "dm_555"}

    resp_msg = MagicMock()
    resp_msg.status_code = 200

    mock_post.side_effect = [resp_dm, resp_msg]

    notifier = DiscordDMNotifier(
        bot_token="test_token",
        user_ids=["111222"],
    )

    tp = TrackedPlayer(253802, "Virat Kohli")
    bstate = BatterState(player_id=253802, name="Virat Kohli", runs=35, balls=18, on_strike=True)
    alert = BattingAlert(
        match_id=5,
        title="IND v AUS",
        url="https://cricinfo.com/5",
        innings=1,
        batting_team="IND",
        score="IND 70/1 (8.0)",
        batters=((tp, bstate),),
    )
    notifier.notify_batting(alert)

    assert mock_post.call_count == 2
    msg_call_kwargs = mock_post.call_args_list[1][1]
    embed = msg_call_kwargs["json"]["embeds"][0]
    assert "Virat Kohli at the crease" in embed["title"]
    assert "(on strike)" in embed["fields"][0]["name"]
    assert "35*" in embed["fields"][0]["value"]


@patch("requests.post")
def test_discord_dm_admin_alert_routing(mock_post: MagicMock) -> None:
    resp_dm = MagicMock()
    resp_dm.status_code = 200
    resp_dm.json.return_value = {"id": "dm_admin"}

    resp_msg = MagicMock()
    resp_msg.status_code = 200

    mock_post.side_effect = [resp_dm, resp_msg]

    notifier = DiscordDMNotifier(
        bot_token="test_token",
        user_ids=["111", "222"],
        admin_user_id="999",
    )
    notifier.alert("Scraper down alert")

    assert mock_post.call_count == 2
    dm_call_kwargs = mock_post.call_args_list[0][1]
    assert dm_call_kwargs["json"] == {"recipient_id": "999"}
    msg_call_kwargs = mock_post.call_args_list[1][1]
    assert "Scraper down alert" in msg_call_kwargs["json"]["embeds"][0]["description"]


@patch("requests.post")
def test_discord_dm_403_user_dms_disabled_handled(mock_post: MagicMock) -> None:
    resp_dm = MagicMock()
    resp_dm.status_code = 200
    resp_dm.json.return_value = {"id": "dm_closed"}

    resp_msg_403 = MagicMock()
    resp_msg_403.status_code = 403
    resp_msg_403.text = '{"code": 50007, "message": "Cannot send messages to this user"}'

    mock_post.side_effect = [resp_dm, resp_msg_403]

    notifier = DiscordDMNotifier(
        bot_token="test_token",
        user_ids=["123"],
    )
    hit = MatchHit(1, "Title", "Live", "url", (TrackedPlayer(1, "Player"),))
    notifier.notify_lineup(hit)


@patch("time.sleep")
@patch("requests.post")
def test_discord_dm_429_retry(mock_post: MagicMock, mock_sleep: MagicMock) -> None:
    resp_dm_429 = MagicMock()
    resp_dm_429.status_code = 429
    resp_dm_429.json.return_value = {"retry_after": 0.25}

    resp_dm_ok = MagicMock()
    resp_dm_ok.status_code = 200
    resp_dm_ok.json.return_value = {"id": "dm_123"}

    resp_msg_ok = MagicMock()
    resp_msg_ok.status_code = 200

    mock_post.side_effect = [resp_dm_429, resp_dm_ok, resp_msg_ok]

    notifier = DiscordDMNotifier(bot_token="t", user_ids=["u1"])
    hit = MatchHit(1, "T", "L", "U", (TrackedPlayer(1, "P"),))
    notifier.notify_lineup(hit)

    assert mock_post.call_count == 3
    mock_sleep.assert_called_once_with(0.25)


@patch("requests.post")
def test_discord_dm_all_failures_raises(mock_post: MagicMock) -> None:
    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.text = "Internal Server Error"
    mock_post.return_value = resp_500

    notifier = DiscordDMNotifier(bot_token="t", user_ids=["u1"])
    hit = MatchHit(1, "T", "L", "U", (TrackedPlayer(1, "P"),))
    with pytest.raises(DiscordDMError) as exc_info:
        notifier.notify_lineup(hit)
    assert "All Discord DM deliveries failed" in str(exc_info.value)
