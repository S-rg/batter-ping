from __future__ import annotations

import contextlib

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

import json
import logging
import os
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from cricket_tracker.models import LineupStatus, MatchInfo
from cricket_tracker.players import PlayerRegistry

logger = logging.getLogger(__name__)


def compute_lineup_status(
    player_id: int,
    squad_ids: frozenset[int],
    xi_ids: frozenset[int],
) -> LineupStatus:
    if xi_ids:
        return LineupStatus.IN_XI if player_id in xi_ids else LineupStatus.NOT_PLAYING
    if squad_ids:
        return LineupStatus.SQUAD_ONLY if player_id in squad_ids else LineupStatus.NOT_PLAYING
    return LineupStatus.UNKNOWN


class StateStore:
    def __init__(
        self,
        state_file: Path | str,
        squads_recheck_seconds: int = 600,
        xi_recheck_seconds: int = 300,
        evict_after_seconds: int = 21600,
    ) -> None:
        self.state_file = Path(state_file).resolve()
        self.lock_file = self.state_file.with_name(f"{self.state_file.name}.lock")
        self.squads_recheck_seconds = squads_recheck_seconds
        self.xi_recheck_seconds = xi_recheck_seconds
        self.evict_after_seconds = evict_after_seconds
        self.data: dict[str, Any] = self._empty_state()
        self._lock_fd: int | None = None

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 1,
            "matches": {},
            "health": {
                "consecutive_failed_runs": 0,
                "last_success": 0.0,
                "alert_sent": False,
            },
        }

    @contextlib.contextmanager
    def lock(self) -> Generator[bool, None, None]:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
        acquired = False
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._lock_fd = fd
                    acquired = True
                except (BlockingIOError, OSError):
                    acquired = False
            else:
                self._lock_fd = fd
                acquired = True

            yield acquired
        finally:
            if acquired and fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
            self._lock_fd = None

    def load(self) -> None:
        if not self.state_file.is_file():
            self.data = self._empty_state()
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                content = f.read()
            if not content.strip():
                self.data = self._empty_state()
                return
            parsed = json.loads(content)
            if not isinstance(parsed, dict) or "matches" not in parsed:
                raise ValueError("Missing 'matches' key in state JSON")
            self.data = parsed
        except Exception as e:
            ts = int(time.time())
            corrupt_file = self.state_file.with_name(f"{self.state_file.name}.corrupt-{ts}")
            logger.error(
                "State file %s is corrupt (%s). Renaming to %s", self.state_file, e, corrupt_file
            )
            try:
                self.state_file.rename(corrupt_file)
            except OSError as ren_err:
                logger.error("Failed to rename corrupt state file: %s", ren_err)
            self.data = self._empty_state()

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        dir_name = self.state_file.parent
        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=dir_name, delete=False, encoding="utf-8"
            ) as tf:
                temp_path = Path(tf.name)
                json.dump(self.data, tf, indent=2)
                tf.flush()
                os.fsync(tf.fileno())

            temp_path.replace(self.state_file)
        except Exception:
            if temp_path and temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()
            raise

    def mark_seen_live(self, live_ids: list[int], now: float) -> None:
        matches = self.data.setdefault("matches", {})
        for mid in live_ids:
            key = str(mid)
            if key in matches:
                matches[key]["last_seen_live"] = now

    def reconcile(self, registry: PlayerRegistry) -> None:
        matches = self.data.get("matches", {})
        tracked_ids = registry.ids

        for m_record in matches.values():
            squad_ids = frozenset(m_record.get("squad_ids", []))
            xi_ids = frozenset(m_record.get("xi_ids", []))
            players = m_record.setdefault("players", {})

            for pid_str in list(players.keys()):
                if int(pid_str) not in tracked_ids:
                    del players[pid_str]

            for pid in tracked_ids:
                pid_str = str(pid)
                if pid_str not in players:
                    status = compute_lineup_status(pid, squad_ids, xi_ids)
                    players[pid_str] = {
                        "status": status.value,
                        "lineup_notified": False,
                        "batting": {"notified_innings": [], "done_innings": []},
                    }

    def put_match(self, info: MatchInfo, registry: PlayerRegistry) -> None:
        key = str(info.match_id)
        matches = self.data.setdefault("matches", {})
        existing = matches.get(key)

        if existing and existing.get("lineup_phase") == "xi_known":
            existing["title"] = info.title
            existing["status"] = info.status
            existing["last_seen_live"] = info.fetched_at
            existing["fetch_failures"] = 0
            return

        if info.xi_ids:
            lineup_phase = "xi_known"
        elif info.squad_ids:
            lineup_phase = "squad_known"
        else:
            lineup_phase = "no_squads"

        existing_players = existing.get("players", {}) if existing else {}
        players_data: dict[str, Any] = {}

        for pid in registry.ids:
            pid_str = str(pid)
            status = compute_lineup_status(pid, info.squad_ids, info.xi_ids)
            prev = existing_players.get(pid_str, {})
            players_data[pid_str] = {
                "status": status.value,
                "lineup_notified": prev.get("lineup_notified", False),
                "batting": prev.get("batting", {"notified_innings": [], "done_innings": []}),
            }

        matches[key] = {
            "title": info.title,
            "status": info.status,
            "url": info.url,
            "format": info.format,
            "max_innings_per_team": info.max_innings_per_team,
            "lineup_phase": lineup_phase,
            "squad_ids": sorted(info.squad_ids),
            "xi_ids": sorted(info.xi_ids),
            "fetched_at": info.fetched_at,
            "last_seen_live": info.fetched_at,
            "fetch_failures": 0,
            "players": players_data,
        }

    def mark_fetch_failure(self, match_id: int) -> None:
        key = str(match_id)
        matches = self.data.setdefault("matches", {})
        if key in matches:
            matches[key]["fetch_failures"] = matches[key].get("fetch_failures", 0) + 1
        else:
            matches[key] = {
                "title": f"Match {match_id}",
                "status": "Unknown",
                "url": f"https://www.espncricinfo.com/matches/engine/match/{match_id}.html",
                "format": "T20",
                "max_innings_per_team": 1,
                "lineup_phase": "no_squads",
                "squad_ids": [],
                "xi_ids": [],
                "fetched_at": 0.0,
                "last_seen_live": time.time(),
                "fetch_failures": 1,
                "players": {},
            }

    def needs_match_fetch(self, match_id: int, now: float) -> bool:
        key = str(match_id)
        matches = self.data.get("matches", {})
        rec = matches.get(key)

        if rec is None:
            return True

        phase = rec.get("lineup_phase", "no_squads")
        fetched_at = rec.get("fetched_at", 0.0)
        age = now - fetched_at
        failures = rec.get("fetch_failures", 0)

        if phase == "no_squads" or failures > 0:
            recheck = self.squads_recheck_seconds * (2 if failures >= 5 else 1)
            return age >= recheck

        players = rec.get("players", {})
        has_squad_only = any(
            p.get("status") == LineupStatus.SQUAD_ONLY.value for p in players.values()
        )

        if phase == "squad_known":
            if has_squad_only:
                return age >= self.xi_recheck_seconds
            return False

        if phase == "xi_known":
            return False

        return False

    def is_hot(self, match_id: int) -> bool:
        key = str(match_id)
        matches = self.data.get("matches", {})
        rec = matches.get(key)
        if rec is None:
            return False

        max_inn = rec.get("max_innings_per_team", 1)
        players = rec.get("players", {})

        for p_rec in players.values():
            if p_rec.get("status") in (LineupStatus.IN_XI.value, LineupStatus.SQUAD_ONLY.value):
                done_innings = p_rec.get("batting", {}).get("done_innings", [])
                if len(done_innings) < max_inn:
                    return True
        return False

    def get_match_record(self, match_id: int) -> dict[str, Any] | None:
        return self.data.get("matches", {}).get(str(match_id))

    def mark_lineup_notified(self, match_id: int, player_id: int) -> None:
        key = str(match_id)
        rec = self.data.get("matches", {}).get(key)
        if rec:
            p_rec = rec.get("players", {}).get(str(player_id))
            if p_rec:
                p_rec["lineup_notified"] = True

    def mark_batting_notified(self, match_id: int, player_id: int, innings: int) -> None:
        key = str(match_id)
        rec = self.data.get("matches", {}).get(key)
        if rec:
            p_rec = rec.get("players", {}).get(str(player_id))
            if p_rec:
                notified = p_rec.setdefault("batting", {}).setdefault("notified_innings", [])
                if innings not in notified:
                    notified.append(innings)

    def mark_innings_done(self, match_id: int, player_id: int, innings: int) -> None:
        key = str(match_id)
        rec = self.data.get("matches", {}).get(key)
        if rec:
            p_rec = rec.get("players", {}).get(str(player_id))
            if p_rec:
                done = p_rec.setdefault("batting", {}).setdefault("done_innings", [])
                if innings not in done:
                    done.append(innings)

    def prune(self, live_ids: list[int], now: float) -> list[int]:
        matches = self.data.get("matches", {})
        evicted: list[int] = []

        for key, rec in list(matches.items()):
            mid = int(key)
            if mid in live_ids:
                continue
            last_seen = rec.get("last_seen_live", rec.get("fetched_at", 0.0))
            max_inn = rec.get("max_innings_per_team", 1)
            effective_evict = (
                max(self.evict_after_seconds, 432000) if max_inn > 1 else self.evict_after_seconds
            )
            if (now - last_seen) >= effective_evict:
                del matches[key]
                evicted.append(mid)

        return evicted

    def record_run_success(self, now: float) -> None:
        health = self.data.setdefault("health", {})
        health["consecutive_failed_runs"] = 0
        health["last_success"] = now

    def record_run_failure(self) -> int:
        health = self.data.setdefault("health", {})
        cnt = health.get("consecutive_failed_runs", 0) + 1
        health["consecutive_failed_runs"] = cnt
        return cnt

    def set_health_alert_sent(self, sent: bool) -> None:
        self.data.setdefault("health", {})["alert_sent"] = sent

    def is_health_alert_sent(self) -> bool:
        return bool(self.data.get("health", {}).get("alert_sent", False))
