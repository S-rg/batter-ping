from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cricket_tracker.cli import main, parse_args, resolve_players_main


def test_cli_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.config == "config.yaml"
    assert args.dry_run is False
    assert args.verbose is False
    assert args.reset_state is False


def test_cli_parse_args_custom() -> None:
    args = parse_args(["-c", "my_cfg.yaml", "--dry-run", "-v", "--reset-state"])
    assert args.config == "my_cfg.yaml"
    assert args.dry_run is True
    assert args.verbose is True
    assert args.reset_state is True


def test_cli_missing_config_returns_code_2() -> None:
    code = main(["--config", "non_existent_config_file_999.yaml"])
    assert code == 2


@patch("cricket_tracker.cli.EspnSource")
def test_cli_dry_run_success(mock_source_cls: MagicMock, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
state_file: ./state.json
max_workers: 2
squads_recheck_seconds: 600
xi_recheck_seconds: 300
evict_after_seconds: 21600
alert_after_failed_runs: 3
request_timeout_seconds: 10
notify:
  lineup: true
  batting: true
notifier:
  type: discord_dm
  bot_token: ""
  user_ids: []
players:
  - { id: 100, name: Player One }
""",
        encoding="utf-8",
    )

    mock_src = MagicMock()
    mock_src.get_live_match_ids.return_value = []
    mock_source_cls.return_value = mock_src

    code = main(["--config", str(cfg_file), "--dry-run"])
    assert code == 0


def test_cli_reset_state(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.json.lock"
    corrupt_file = tmp_path / "state.json.corrupt-1234"

    cfg_file.write_text(
        f"""
state_file: {state_file}
notifier:
  type: console
players: []
""",
        encoding="utf-8",
    )
    state_file.write_text("{}", encoding="utf-8")
    lock_file.write_text("", encoding="utf-8")
    corrupt_file.write_text("bad json", encoding="utf-8")

    assert state_file.exists()
    assert lock_file.exists()
    assert corrupt_file.exists()

    code = main(["--config", str(cfg_file), "--reset-state"])
    assert code == 0
    assert not state_file.exists()
    assert not lock_file.exists()
    assert not corrupt_file.exists()


@patch("espncricinfo.player.Player")
def test_resolve_players_main(
    mock_player_cls: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    p_instance = MagicMock()
    p_instance.name = "Virat Kohli"
    p_instance.full_name = "Virat Kohli"
    mock_player_cls.return_value = p_instance

    code = resolve_players_main(["253802"])
    assert code == 0
    captured = capsys.readouterr()
    assert "players:" in captured.out
    assert "id: 253802, name: Virat Kohli" in captured.out
