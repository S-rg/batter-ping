from __future__ import annotations

from pathlib import Path

from cricket_tracker.models import (
    BatterState,
    LineupStatus,
    LiveBatting,
    MatchInfo,
    TrackedPlayer,
)
from cricket_tracker.players import PlayerRegistry
from cricket_tracker.source import SourceError
from cricket_tracker.state import StateStore
from cricket_tracker.tracker import Tracker

from .fakes import FakeSource, RecordingNotifier


def make_match_info(
    match_id: int,
    title: str = "IND v AUS",
    format_str: str = "T20I",
    max_innings: int = 1,
    squad_ids: list[int] | None = None,
    xi_ids: list[int] | None = None,
    fetched_at: float = 1000.0,
) -> MatchInfo:
    return MatchInfo(
        match_id=match_id,
        title=title,
        status="Live",
        url=f"https://cricinfo.com/{match_id}",
        format=format_str,
        max_innings_per_team=max_innings,
        squad_ids=frozenset(squad_ids or []),
        xi_ids=frozenset(xi_ids or []),
        fetched_at=fetched_at,
    )


def test_lineup_transitions_and_alerts(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(100, "Virat Kohli")])
    notifier = RecordingNotifier()

    m1_squad = make_match_info(1, squad_ids=[100, 200, 300], xi_ids=[], fetched_at=1000.0)
    source = FakeSource(live_ids=[1], matches={1: m1_squad})

    clock_time = 1000.0
    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: clock_time,
    )

    res1 = tracker.run()
    assert res1.lineup_alerts == 0
    assert len(notifier.lineup_hits) == 0
    rec = state.get_match_record(1)
    assert rec["players"]["100"]["status"] == LineupStatus.SQUAD_ONLY.value

    clock_time = 1350.0
    m1_xi = make_match_info(1, squad_ids=[100, 200, 300], xi_ids=[100, 200], fetched_at=clock_time)
    source.matches[1] = m1_xi

    res2 = tracker.run()
    assert res2.lineup_alerts == 1
    assert len(notifier.lineup_hits) == 1
    hit = notifier.lineup_hits[0]
    assert hit.match_id == 1
    assert hit.players[0].id == 100
    rec = state.get_match_record(1)
    assert rec["players"]["100"]["status"] == LineupStatus.IN_XI.value
    assert rec["players"]["100"]["lineup_notified"] is True

    clock_time += 60.0
    res3 = tracker.run()
    assert res3.lineup_alerts == 0
    assert len(notifier.lineup_hits) == 1
    assert source.get_match_info_calls[1] == 2


def test_untracked_match_skip_and_reconcile(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(999, "Special Player")])
    notifier = RecordingNotifier()

    m10 = make_match_info(10, squad_ids=[10, 20, 30], xi_ids=[10, 20], fetched_at=1000.0)
    source = FakeSource(live_ids=[10], matches={10: m10})

    clock_time = 1000.0
    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: clock_time,
    )

    res1 = tracker.run()
    assert res1.lineups_fetched == 1
    assert source.get_match_info_calls[10] == 1

    clock_time += 1000.0
    res2 = tracker.run()
    assert res2.lineups_fetched == 0
    assert source.get_match_info_calls[10] == 1

    registry_updated = PlayerRegistry(
        [
            TrackedPlayer(999, "Special Player"),
            TrackedPlayer(10, "Player 10"),
        ]
    )
    tracker.registry = registry_updated

    res3 = tracker.run()
    assert res3.lineups_fetched == 0
    assert source.get_match_info_calls[10] == 1
    assert res3.lineup_alerts == 1
    assert notifier.lineup_hits[-1].players[0].id == 10


def test_not_playing_produces_no_alerts_and_not_hot(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(300, "Benched Player")])
    notifier = RecordingNotifier()

    m20 = make_match_info(20, squad_ids=[100, 200, 300], xi_ids=[100, 200], fetched_at=1000.0)
    source = FakeSource(live_ids=[20], matches={20: m20})

    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    res = tracker.run()
    assert res.lineup_alerts == 0
    assert res.hot_matches == 0
    assert len(notifier.lineup_hits) == 0
    assert state.is_hot(20) is False


def test_batting_alerts_and_innings_bookkeeping_t20(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(100, "Star Batter")])
    notifier = RecordingNotifier()

    m30 = make_match_info(
        30, format_str="T20I", max_innings=1, xi_ids=[100, 200], fetched_at=1000.0
    )
    source = FakeSource(live_ids=[30], matches={30: m30})

    clock_time = 1000.0
    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: clock_time,
    )

    res1 = tracker.run()
    assert res1.lineup_alerts == 1
    assert state.is_hot(30) is True

    bstate_100 = BatterState(player_id=100, name="Star Batter", runs=12, balls=6, on_strike=True)
    live_batting_1 = LiveBatting(
        match_id=30,
        innings=1,
        batting_team="IND",
        score="IND 45/2 (5.4)",
        batters=(bstate_100,),
    )
    source.battings[30] = live_batting_1

    res2 = tracker.run()
    assert res2.batting_alerts == 1
    assert len(notifier.batting_alerts) == 1
    alert = notifier.batting_alerts[0]
    assert alert.match_id == 30
    assert alert.batters[0][0].id == 100
    assert alert.batters[0][1].runs == 12

    bstate_100_still_batting = BatterState(
        player_id=100, name="Star Batter", runs=35, balls=18, on_strike=False
    )
    source.battings[30] = LiveBatting(
        match_id=30,
        innings=1,
        batting_team="IND",
        score="IND 85/2 (10.0)",
        batters=(bstate_100_still_batting,),
    )
    res3 = tracker.run()
    assert res3.batting_alerts == 0
    assert len(notifier.batting_alerts) == 1
    assert state.is_hot(30) is True

    other_batter = BatterState(player_id=200, name="Other", runs=4, balls=3, on_strike=True)
    source.battings[30] = LiveBatting(
        match_id=30,
        innings=1,
        batting_team="IND",
        score="IND 92/3 (11.2)",
        batters=(other_batter,),
    )
    res4 = tracker.run()
    assert res4.batting_alerts == 0
    rec = state.get_match_record(30)
    assert 1 in rec["players"]["100"]["batting"]["done_innings"]
    assert state.is_hot(30) is False


def test_test_match_multi_innings(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(50, "Test Batter")])
    notifier = RecordingNotifier()

    m40 = make_match_info(40, format_str="Test", max_innings=2, xi_ids=[50], fetched_at=1000.0)
    source = FakeSource(live_ids=[40], matches={40: m40})

    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    tracker.run()

    b_inn1 = BatterState(player_id=50, name="Test Batter", runs=10, balls=20, on_strike=True)
    source.battings[40] = LiveBatting(
        40, innings=1, batting_team="ENG", score="ENG 25/1", batters=(b_inn1,)
    )
    res2 = tracker.run()
    assert res2.batting_alerts == 1

    source.battings[40] = LiveBatting(
        40, innings=1, batting_team="ENG", score="ENG 80/2", batters=()
    )
    tracker.run()
    assert state.is_hot(40) is True

    b_inn3 = BatterState(player_id=50, name="Test Batter", runs=4, balls=5, on_strike=False)
    source.battings[40] = LiveBatting(
        40, innings=3, batting_team="ENG", score="ENG 10/0", batters=(b_inn3,)
    )
    res4 = tracker.run()
    assert res4.batting_alerts == 1

    source.battings[40] = LiveBatting(
        40, innings=3, batting_team="ENG", score="ENG 50/1", batters=()
    )
    tracker.run()
    assert state.is_hot(40) is False


def test_isolated_fetch_failures(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(1, "P1")])
    notifier = RecordingNotifier()

    m1 = make_match_info(1, xi_ids=[1])
    m2 = make_match_info(2, xi_ids=[1])
    source = FakeSource(
        live_ids=[1, 2],
        matches={1: m1, 2: m2},
        battings={1: LiveBatting(1, innings=1, batting_team="IND", score="0/0", batters=())},
    )
    source.raise_on_match_info[2] = SourceError("Network timeout on match 2", match_id=2)

    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    res = tracker.run()
    assert res.lineups_fetched == 1
    assert res.lineup_alerts == 1
    assert len(res.errors) == 1
    assert "Match 2 lineup error" in res.errors[0]
    rec2 = state.get_match_record(2)
    assert rec2["fetch_failures"] == 1


def test_live_list_failure_does_not_prune(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file, evict_after_seconds=100)
    registry = PlayerRegistry([TrackedPlayer(1, "P1")])
    notifier = RecordingNotifier()

    m1 = make_match_info(1, xi_ids=[1], fetched_at=1000.0)
    source = FakeSource(live_ids=[1], matches={1: m1})

    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )
    tracker.run()
    assert state.get_match_record(1) is not None

    source.raise_on_live_ids = SourceError("ESPN down")
    res = tracker.run()
    assert res.live_list_ok is False
    assert state.get_match_record(1) is not None
    assert state.data["health"]["consecutive_failed_runs"] == 1


def test_notifier_failure_retries_next_run(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(1, "P1")])
    notifier = RecordingNotifier(fail_lineup=True)

    m1 = make_match_info(1, xi_ids=[1])
    source = FakeSource(
        live_ids=[1],
        matches={1: m1},
        battings={1: LiveBatting(1, innings=1, batting_team="IND", score="0/0", batters=())},
    )

    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    res1 = tracker.run()
    assert len(res1.errors) == 1
    rec = state.get_match_record(1)
    assert rec["players"]["1"]["lineup_notified"] is False

    notifier.fail_lineup = False
    res2 = tracker.run()
    assert res2.lineup_alerts == 1
    assert len(notifier.lineup_hits) == 1
    rec = state.get_match_record(1)
    assert rec["players"]["1"]["lineup_notified"] is True


def test_batter_not_prematurely_marked_done_without_dismissal(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(100, "Star Batter")])
    notifier = RecordingNotifier()

    m1 = make_match_info(1, xi_ids=[100])
    source = FakeSource(
        live_ids=[1],
        matches={1: m1},
    )
    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    b100 = BatterState(player_id=100, name="Star Batter", runs=10, balls=5, on_strike=True)
    source.battings[1] = LiveBatting(
        1,
        innings=1,
        batting_team="IND",
        score="20/0",
        batters=(b100,),
        dismissed_player_ids=frozenset(),
    )
    tracker.run()
    assert len(notifier.batting_alerts) == 1

    other = BatterState(player_id=200, name="Other", runs=5, balls=4, on_strike=False)
    source.battings[1] = LiveBatting(
        1,
        innings=1,
        batting_team="IND",
        score="25/0",
        batters=(other,),
        dismissed_player_ids=frozenset({300}),
    )
    tracker.run()

    rec = state.get_match_record(1)
    assert 1 not in rec["players"]["100"]["batting"]["done_innings"]
    assert state.is_hot(1) is True

    source.battings[1] = LiveBatting(
        1,
        innings=1,
        batting_team="IND",
        score="30/1",
        batters=(other,),
        dismissed_player_ids=frozenset({100}),
    )
    tracker.run()
    rec = state.get_match_record(1)
    assert 1 in rec["players"]["100"]["batting"]["done_innings"]
    assert state.is_hot(1) is False


def test_substitute_impact_player_alert_on_batting(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = StateStore(state_file)
    registry = PlayerRegistry([TrackedPlayer(500, "Impact Player")])
    notifier = RecordingNotifier()

    m1 = make_match_info(1, squad_ids=[500, 1, 2], xi_ids=[1, 2])
    source = FakeSource(
        live_ids=[1],
        matches={1: m1},
    )
    tracker = Tracker(
        source=source,
        state=state,
        registry=registry,
        notifier=notifier,
        clock=lambda: 1000.0,
    )

    tracker.run()
    rec = state.get_match_record(1)
    assert rec["players"]["500"]["status"] == LineupStatus.NOT_PLAYING.value
    sub_batter = BatterState(player_id=500, name="Impact Player", runs=15, balls=8, on_strike=True)
    source.battings[1] = LiveBatting(
        1, innings=1, batting_team="IND", score="80/3", batters=(sub_batter,)
    )

    rec["players"]["500"]["status"] = LineupStatus.SQUAD_ONLY.value

    tracker.run()
    assert len(notifier.batting_alerts) == 1
    assert notifier.batting_alerts[0].batters[0][0].id == 500
    assert rec["players"]["500"]["status"] == LineupStatus.IN_XI.value
