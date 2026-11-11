from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from cricket_tracker.models import (
    BatterState,
    BattingAlert,
    LineupStatus,
    LiveBatting,
    MatchHit,
    MatchInfo,
    RunResult,
    TrackedPlayer,
)
from cricket_tracker.notifiers.base import Notifier
from cricket_tracker.players import PlayerRegistry
from cricket_tracker.source import Source, SourceError
from cricket_tracker.state import StateStore

logger = logging.getLogger(__name__)


class Tracker:
    def __init__(
        self,
        source: Source,
        state: StateStore,
        registry: PlayerRegistry,
        notifier: Notifier,
        notify_lineup: bool = True,
        notify_batting: bool = True,
        max_workers: int = 8,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.source = source
        self.state = state
        self.registry = registry
        self.notifier = notifier
        self.notify_lineup = notify_lineup
        self.notify_batting = notify_batting
        self.max_workers = max_workers
        self.clock = clock

    def _plan_workers(self, fetches: int) -> int:
        """Threads for a batch: what the source can usefully run at once, capped."""
        workers = min(self.max_workers, fetches)
        plan = getattr(self.source, "plan_concurrency", None)
        if plan is not None:
            try:
                workers = min(workers, max(1, int(plan(fetches))))
            except Exception:
                logger.exception("Concurrency planning failed; using %d workers", workers)
        return workers

    def run(self) -> RunResult:
        now = self.clock()
        result = RunResult()

        try:
            live_ids = self.source.get_live_match_ids()
            result.live_count = len(live_ids)
            result.live_list_ok = True
        except SourceError as e:
            logger.error("Live match list fetch failed: %s", e)
            result.live_list_ok = False
            result.errors.append(str(e))
            self.state.record_run_failure()
            return result

        self.state.mark_seen_live(live_ids, now)
        self.state.reconcile(self.registry)

        to_fetch = [m for m in live_ids if self.state.needs_match_fetch(m, now)]
        if to_fetch:
            workers = self._plan_workers(len(to_fetch))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_mid = {
                    executor.submit(self.source.get_match_info, mid): mid for mid in to_fetch
                }
                for future in as_completed(future_to_mid):
                    mid = future_to_mid[future]
                    try:
                        info: MatchInfo = future.result()
                        self.state.put_match(info, self.registry)
                        result.lineups_fetched += 1
                    except SourceError as e:
                        logger.warning("Lineup fetch failed for match %d: %s", mid, e)
                        self.state.mark_fetch_failure(mid)
                        result.errors.append(f"Match {mid} lineup error: {e}")
                    except Exception:
                        logger.exception("Unexpected error fetching match %d", mid)
                        self.state.mark_fetch_failure(mid)
                        result.errors.append(f"Match {mid} unexpected error")

        if self.notify_lineup:
            for mid in live_ids:
                rec = self.state.get_match_record(mid)
                if not rec:
                    continue

                unannounced_players: list[TrackedPlayer] = []
                for pid_str, p_rec in rec.get("players", {}).items():
                    if p_rec.get("status") == LineupStatus.IN_XI.value and not p_rec.get(
                        "lineup_notified", False
                    ):
                        player = self.registry.get(int(pid_str))
                        if player:
                            unannounced_players.append(player)

                if unannounced_players:
                    hit = MatchHit(
                        match_id=mid,
                        title=rec.get("title", f"Match {mid}"),
                        status=rec.get("status", "Live"),
                        url=rec.get(
                            "url", f"https://www.espncricinfo.com/matches/engine/match/{mid}.html"
                        ),
                        players=tuple(unannounced_players),
                    )
                    try:
                        self.notifier.notify_lineup(hit)
                        for p in unannounced_players:
                            self.state.mark_lineup_notified(mid, p.id)
                        result.lineup_alerts += 1
                    except Exception as e:
                        logger.error("Failed to send lineup notification for match %d: %s", mid, e)
                        result.errors.append(f"Lineup notification failed for match {mid}: {e}")

        hot_matches = [m for m in live_ids if self.state.is_hot(m)]
        result.hot_matches = len(hot_matches)

        live_batting_map: dict[int, LiveBatting] = {}
        if hot_matches:
            workers = self._plan_workers(len(hot_matches))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_mid = {
                    executor.submit(self.source.get_live_batting, mid): mid for mid in hot_matches
                }
                for future in as_completed(future_to_mid):
                    mid = future_to_mid[future]
                    try:
                        live_batting_map[mid] = future.result()
                    except SourceError as e:
                        logger.warning("Batting fetch failed for hot match %d: %s", mid, e)
                        result.errors.append(f"Match {mid} batting error: {e}")
                    except Exception:
                        logger.exception("Unexpected error fetching batting for match %d", mid)
                        result.errors.append(f"Match {mid} unexpected batting error")

        if self.notify_batting:
            for mid, live_batting in live_batting_map.items():
                rec = self.state.get_match_record(mid)
                if not rec:
                    continue

                players_at_crease: list[tuple[TrackedPlayer, BatterState]] = []
                for bstate in live_batting.batters:
                    pid = bstate.player_id
                    p_rec = rec.get("players", {}).get(str(pid))
                    if not p_rec:
                        continue
                    notified_innings = p_rec.get("batting", {}).get("notified_innings", [])
                    if live_batting.innings not in notified_innings:
                        player = self.registry.get(pid)
                        if player:
                            players_at_crease.append((player, bstate))
                            p_rec["status"] = LineupStatus.IN_XI.value

                if players_at_crease:
                    alert = BattingAlert(
                        match_id=mid,
                        title=rec.get("title", f"Match {mid}"),
                        url=rec.get(
                            "url", f"https://www.espncricinfo.com/matches/engine/match/{mid}.html"
                        ),
                        innings=live_batting.innings,
                        batting_team=live_batting.batting_team,
                        score=live_batting.score,
                        batters=tuple(players_at_crease),
                    )
                    try:
                        self.notifier.notify_batting(alert)
                        for player, _ in players_at_crease:
                            self.state.mark_batting_notified(mid, player.id, live_batting.innings)
                        result.batting_alerts += 1
                    except Exception as e:
                        logger.error("Failed to send batting alert for match %d: %s", mid, e)
                        result.errors.append(f"Batting notification failed for match {mid}: {e}")

        for mid, live_batting in live_batting_map.items():
            rec = self.state.get_match_record(mid)
            if not rec:
                continue

            current_crease_pids = {bstate.player_id for bstate in live_batting.batters}
            dismissed_pids = getattr(live_batting, "dismissed_player_ids", frozenset())

            for pid_str, p_rec in rec.get("players", {}).items():
                if p_rec.get("status") != LineupStatus.IN_XI.value:
                    continue

                pid = int(pid_str)
                batting_info = p_rec.get("batting", {})
                notified_innings = list(batting_info.get("notified_innings", []))
                done_innings = set(batting_info.get("done_innings", []))

                for inn in notified_innings:
                    if inn in done_innings:
                        continue
                    is_dismissed = (
                        (pid in dismissed_pids)
                        if dismissed_pids
                        else (pid not in current_crease_pids)
                    )
                    if live_batting.innings > inn or (live_batting.innings == inn and is_dismissed):
                        self.state.mark_innings_done(mid, pid, inn)

        self.state.prune(live_ids, now)
        self.state.record_run_success(now)

        return result
