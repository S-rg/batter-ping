from __future__ import annotations

from cricket_tracker.models import BattingAlert, LiveBatting, MatchHit, MatchInfo
from cricket_tracker.source import SourceError


class FakeSource:
    def __init__(
        self,
        live_ids: list[int] | None = None,
        matches: dict[int, MatchInfo] | None = None,
        battings: dict[int, LiveBatting] | None = None,
    ) -> None:
        self.live_ids: list[int] = live_ids or []
        self.matches: dict[int, MatchInfo] = matches or {}
        self.battings: dict[int, LiveBatting] = battings or {}

        self.raise_on_live_ids: Exception | None = None
        self.raise_on_match_info: dict[int, Exception] = {}
        self.raise_on_live_batting: dict[int, Exception] = {}

        self.get_live_ids_calls: int = 0
        self.get_match_info_calls: dict[int, int] = {}
        self.get_live_batting_calls: dict[int, int] = {}

    def get_live_match_ids(self) -> list[int]:
        self.get_live_ids_calls += 1
        if self.raise_on_live_ids is not None:
            raise self.raise_on_live_ids
        return list(self.live_ids)

    def get_match_info(self, match_id: int) -> MatchInfo:
        self.get_match_info_calls[match_id] = self.get_match_info_calls.get(match_id, 0) + 1
        if match_id in self.raise_on_match_info:
            raise self.raise_on_match_info[match_id]
        if match_id not in self.matches:
            raise SourceError(f"Match {match_id} not found in FakeSource", match_id=match_id)
        return self.matches[match_id]

    def get_live_batting(self, match_id: int) -> LiveBatting:
        self.get_live_batting_calls[match_id] = self.get_live_batting_calls.get(match_id, 0) + 1
        if match_id in self.raise_on_live_batting:
            raise self.raise_on_live_batting[match_id]
        if match_id not in self.battings:
            raise SourceError(
                f"Batting info for {match_id} not found in FakeSource", match_id=match_id
            )
        return self.battings[match_id]


class RecordingNotifier:
    def __init__(
        self,
        fail_lineup: bool = False,
        fail_batting: bool = False,
        fail_alert: bool = False,
    ) -> None:
        self.lineup_hits: list[MatchHit] = []
        self.batting_alerts: list[BattingAlert] = []
        self.admin_alerts: list[str] = []

        self.fail_lineup = fail_lineup
        self.fail_batting = fail_batting
        self.fail_alert = fail_alert

    def notify_lineup(self, hit: MatchHit) -> None:
        if self.fail_lineup:
            raise RuntimeError("Simulated lineup notification failure")
        self.lineup_hits.append(hit)

    def notify_batting(self, alert: BattingAlert) -> None:
        if self.fail_batting:
            raise RuntimeError("Simulated batting notification failure")
        self.batting_alerts.append(alert)

    def alert(self, message: str) -> None:
        if self.fail_alert:
            raise RuntimeError("Simulated admin alert failure")
        self.admin_alerts.append(message)
