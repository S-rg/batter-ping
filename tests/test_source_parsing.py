from __future__ import annotations

import json
from pathlib import Path

from cricket_tracker.source import parse_live_batting, parse_match_info

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_match_info_settled_t20() -> None:
    fixture_path = FIXTURES_DIR / "match_1555121.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    info = parse_match_info(data, match_id=1555121, now=1700000000.0)

    assert info.match_id == 1555121
    assert "Afghanistan" in info.title or "Zimbabwe" in info.title
    assert info.format == "T20I"
    assert info.max_innings_per_team == 1
    assert len(info.squad_ids) == 22
    assert len(info.xi_ids) == 22
    assert 974087 in info.xi_ids


def test_parse_live_batting_with_crease_batters() -> None:
    fixture_path = FIXTURES_DIR / "match_1559201.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    live = parse_live_batting(data, match_id=1559201)

    assert live.match_id == 1559201
    assert live.innings == 2
    assert len(live.batters) == 2

    striker = next(b for b in live.batters if b.on_strike)
    assert striker.player_id == 641385
    assert striker.runs == 50
    assert striker.balls == 22

    non_striker = next(b for b in live.batters if not b.on_strike)
    assert non_striker.player_id == 571102
    assert non_striker.runs == 7
    assert non_striker.balls == 4


def test_parse_match_info_squad_only() -> None:
    fixture_path = FIXTURES_DIR / "match_squad_only.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    info = parse_match_info(data, match_id=99999, now=1700000000.0)
    assert len(info.squad_ids) > 11
    assert len(info.xi_ids) == 0


def test_parse_match_info_no_squads() -> None:
    fixture_path = FIXTURES_DIR / "match_no_squads.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    info = parse_match_info(data, match_id=88888, now=1700000000.0)
    assert len(info.squad_ids) == 0
    assert len(info.xi_ids) == 0
