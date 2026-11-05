from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LineupStatus(str, Enum):
    UNKNOWN = "unknown"
    SQUAD_ONLY = "squad_only"
    IN_XI = "in_xi"
    NOT_PLAYING = "not_playing"


@dataclass(frozen=True)
class TrackedPlayer:
    id: int
    name: str


@dataclass
class MatchInfo:
    match_id: int
    title: str
    status: str
    url: str
    format: str
    max_innings_per_team: int
    squad_ids: frozenset[int]
    xi_ids: frozenset[int]
    fetched_at: float


@dataclass(frozen=True)
class BatterState:
    player_id: int
    name: str
    runs: int
    balls: int
    on_strike: bool


@dataclass(frozen=True)
class LiveBatting:
    match_id: int
    innings: int
    batting_team: str
    score: str
    batters: tuple[BatterState, ...]
    dismissed_player_ids: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class MatchHit:
    match_id: int
    title: str
    status: str
    url: str
    players: tuple[TrackedPlayer, ...]


@dataclass(frozen=True)
class BattingAlert:
    match_id: int
    title: str
    url: str
    innings: int
    batting_team: str
    score: str
    batters: tuple[tuple[TrackedPlayer, BatterState], ...]


@dataclass
class RunResult:
    live_count: int = 0
    lineups_fetched: int = 0
    hot_matches: int = 0
    lineup_alerts: int = 0
    batting_alerts: int = 0
    errors: list[str] = field(default_factory=list)
    live_list_ok: bool = True
