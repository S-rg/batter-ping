from __future__ import annotations

from pathlib import Path

from cricket_tracker.models import LineupStatus, MatchInfo, TrackedPlayer
from cricket_tracker.players import PlayerRegistry
from cricket_tracker.state import StateStore, compute_lineup_status


def test_compute_lineup_status() -> None:
    squad = frozenset([1, 2, 3])
    xi = frozenset([1, 2])
    assert compute_lineup_status(1, squad, xi) == LineupStatus.IN_XI
    assert compute_lineup_status(3, squad, xi) == LineupStatus.NOT_PLAYING
    assert compute_lineup_status(99, squad, xi) == LineupStatus.NOT_PLAYING

    assert compute_lineup_status(1, squad, frozenset()) == LineupStatus.SQUAD_ONLY
    assert compute_lineup_status(99, squad, frozenset()) == LineupStatus.NOT_PLAYING

    assert compute_lineup_status(1, frozenset(), frozenset()) == LineupStatus.UNKNOWN


def test_atomic_save_and_load(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    store.load()
    assert store.data["version"] == 1
    assert store.data["matches"] == {}

    info = MatchInfo(
        match_id=101,
        title="India v Australia",
        status="Live",
        url="https://cricinfo.com/101",
        format="T20I",
        max_innings_per_team=1,
        squad_ids=frozenset([10, 20]),
        xi_ids=frozenset([10, 20]),
        fetched_at=1000.0,
    )
    registry = PlayerRegistry([TrackedPlayer(10, "Player 10")])
    store.put_match(info, registry)
    store.save()

    assert state_file.is_file()

    fresh = StateStore(state_file)
    fresh.load()
    assert "101" in fresh.data["matches"]
    rec = fresh.data["matches"]["101"]
    assert rec["title"] == "India v Australia"
    assert rec["players"]["10"]["status"] == LineupStatus.IN_XI.value


def test_corrupt_file_recovery(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{ this is corrupt json !!!", encoding="utf-8")

    store = StateStore(state_file)
    store.load()

    assert store.data["matches"] == {}

    corrupt_files = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text(encoding="utf-8") == "{ this is corrupt json !!!"


def test_lock_contention(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store1 = StateStore(state_file)
    store2 = StateStore(state_file)

    with store1.lock() as acquired1:
        assert acquired1 is True
        with store2.lock() as acquired2:
            assert acquired2 is False

    with store2.lock() as acquired2_again:
        assert acquired2_again is True


def test_reconcile_with_player_registry(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    store.load()

    info = MatchInfo(
        match_id=202,
        title="Test Match",
        status="Live",
        url="https://cricinfo.com/202",
        format="Test",
        max_innings_per_team=2,
        squad_ids=frozenset([1, 2, 3]),
        xi_ids=frozenset([1, 2]),
        fetched_at=1000.0,
    )
    reg1 = PlayerRegistry([TrackedPlayer(1, "Player 1")])
    store.put_match(info, reg1)

    rec = store.get_match_record(202)
    assert rec is not None
    assert set(rec["players"].keys()) == {"1"}
    assert rec["players"]["1"]["status"] == LineupStatus.IN_XI.value

    reg2 = PlayerRegistry(
        [
            TrackedPlayer(1, "Player 1"),
            TrackedPlayer(2, "Player 2"),
            TrackedPlayer(3, "Player 3"),
        ]
    )
    store.reconcile(reg2)

    rec = store.get_match_record(202)
    assert rec is not None
    assert set(rec["players"].keys()) == {"1", "2", "3"}
    assert rec["players"]["1"]["status"] == LineupStatus.IN_XI.value
    assert rec["players"]["2"]["status"] == LineupStatus.IN_XI.value
    assert rec["players"]["3"]["status"] == LineupStatus.NOT_PLAYING.value


def test_needs_match_fetch_rules(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(
        state_file,
        squads_recheck_seconds=600,
        xi_recheck_seconds=300,
    )
    store.load()

    assert store.needs_match_fetch(999, now=1000.0) is True

    no_squads_info = MatchInfo(
        match_id=1,
        title="M1",
        status="Scheduled",
        url="",
        format="T20",
        max_innings_per_team=1,
        squad_ids=frozenset(),
        xi_ids=frozenset(),
        fetched_at=1000.0,
    )
    registry = PlayerRegistry([TrackedPlayer(10, "P10")])
    store.put_match(no_squads_info, registry)

    assert store.needs_match_fetch(1, now=1200.0) is False
    assert store.needs_match_fetch(1, now=1601.0) is True

    squad_known_info = MatchInfo(
        match_id=2,
        title="M2",
        status="Scheduled",
        url="",
        format="T20",
        max_innings_per_team=1,
        squad_ids=frozenset([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]),
        xi_ids=frozenset(),
        fetched_at=1000.0,
    )
    store.put_match(squad_known_info, registry)
    assert store.needs_match_fetch(2, now=1100.0) is False
    assert store.needs_match_fetch(2, now=1301.0) is True

    untracked_squad_info = MatchInfo(
        match_id=3,
        title="M3",
        status="Scheduled",
        url="",
        format="T20",
        max_innings_per_team=1,
        squad_ids=frozenset([90, 91, 92]),
        xi_ids=frozenset(),
        fetched_at=1000.0,
    )
    store.put_match(untracked_squad_info, registry)
    assert store.needs_match_fetch(3, now=2000.0) is False

    xi_known_info = MatchInfo(
        match_id=4,
        title="M4",
        status="Live",
        url="",
        format="T20",
        max_innings_per_team=1,
        squad_ids=frozenset([10, 20]),
        xi_ids=frozenset([10, 20]),
        fetched_at=1000.0,
    )
    store.put_match(xi_known_info, registry)
    assert store.needs_match_fetch(4, now=5000.0) is False


def test_prune_grace_period(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file, evict_after_seconds=3600)
    store.load()

    info = MatchInfo(
        match_id=500,
        title="M500",
        status="Live",
        url="",
        format="T20",
        max_innings_per_team=1,
        squad_ids=frozenset([1]),
        xi_ids=frozenset([1]),
        fetched_at=1000.0,
    )
    registry = PlayerRegistry([TrackedPlayer(1, "P1")])
    store.put_match(info, registry)

    evicted = store.prune(live_ids=[], now=2800.0)
    assert evicted == []
    assert store.get_match_record(500) is not None

    evicted = store.prune(live_ids=[], now=4601.0)
    assert evicted == [500]
    assert store.get_match_record(500) is None
