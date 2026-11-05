from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from cricket_tracker.models import BatterState, LiveBatting, MatchInfo

logger = logging.getLogger(__name__)


class SourceError(Exception):
    def __init__(self, message: str, match_id: int | None = None) -> None:
        super().__init__(message)
        self.match_id = match_id


class Source(Protocol):
    def get_live_match_ids(self) -> list[int]: ...

    def get_match_info(self, match_id: int) -> MatchInfo: ...

    def get_live_batting(self, match_id: int) -> LiveBatting: ...


def parse_match_info(data: dict[str, Any], match_id: int, now: float | None = None) -> MatchInfo:
    if now is None:
        now = time.time()

    match_dict = data.get("match", {})
    description = (
        data.get("description") or match_dict.get("cms_match_title") or f"Match {match_id}"
    )
    status = (
        match_dict.get("current_summary")
        or data.get("live", {}).get("status")
        or match_dict.get("match_status")
        or "Live"
    )
    url = f"https://www.espncricinfo.com/matches/engine/match/{match_id}.html"

    intl_class = match_dict.get("international_class_card") or ""
    general_class = match_dict.get("general_class_card") or ""
    fmt = intl_class or general_class or "T20"

    fmt_lower = fmt.lower()
    is_multi_day = any(k in fmt_lower for k in ("test", "first-class", "fc"))
    max_innings_per_team = 2 if is_multi_day else 1

    teams = data.get("team", [])
    team_player_lists: list[list[dict[str, Any]]] = [t.get("player", []) for t in teams]

    all_player_ids: list[int] = []
    non_sub_player_ids: list[int] = []
    has_substitutes_flagged = False

    for p_list in team_player_lists:
        for p_wrap in p_list:
            is_sub = bool(p_wrap.get("isSubstitute"))
            if is_sub:
                has_substitutes_flagged = True
            player_obj = p_wrap.get("player") or {}
            oid = player_obj.get("objectId")
            if oid is not None:
                try:
                    pid = int(oid)
                    all_player_ids.append(pid)
                    if not is_sub:
                        non_sub_player_ids.append(pid)
                except (ValueError, TypeError):
                    continue

    squad_ids = frozenset(all_player_ids)

    toss_winner = match_dict.get("toss_winner_team_id")
    has_toss = bool(toss_winner and str(toss_winner).strip())
    has_innings = bool(data.get("innings"))
    match_status = (match_dict.get("match_status") or "").lower()
    is_live_or_past = match_status in ("live", "result", "in progress", "post")
    all_teams_small = bool(teams) and all(len(pl) <= 11 for pl in team_player_lists if pl)

    mp_team_players = data.get("_match_players", {}).get("teamPlayers", [])
    mp_xi_ids: list[int] = []
    if mp_team_players:
        for tp in mp_team_players:
            tp_players = tp.get("players", [])
            for p_wrap in tp_players:
                if not p_wrap.get("isSubstitute"):
                    p_obj = p_wrap.get("player") or {}
                    p_oid = p_obj.get("objectId")
                    if p_oid is not None:
                        try:
                            mp_xi_ids.append(int(p_oid))
                        except (ValueError, TypeError):
                            pass

    all_mp_teams_small = bool(mp_team_players) and all(
        len(tp.get("players", [])) <= 11 for tp in mp_team_players if tp.get("players")
    )

    if squad_ids:
        if all_teams_small and (has_toss or has_innings or is_live_or_past):
            xi_ids = squad_ids
        elif all_mp_teams_small and (has_toss or has_innings or is_live_or_past):
            xi_ids = frozenset(mp_xi_ids)
        elif (
            has_substitutes_flagged
            and non_sub_player_ids
            and (has_toss or has_innings or is_live_or_past)
        ):
            xi_ids = frozenset(non_sub_player_ids)
        else:
            xi_ids = frozenset()
    else:
        xi_ids = frozenset()

    return MatchInfo(
        match_id=match_id,
        title=description,
        status=status,
        url=url,
        format=fmt,
        max_innings_per_team=max_innings_per_team,
        squad_ids=squad_ids,
        xi_ids=xi_ids,
        fetched_at=now,
    )


def parse_live_batting(data: dict[str, Any], match_id: int) -> LiveBatting:
    innings_list = data.get("innings", [])
    if not innings_list:
        return LiveBatting(
            match_id=match_id,
            innings=1,
            batting_team="",
            score="Match not started",
            batters=(),
            dismissed_player_ids=frozenset(),
        )

    curr_inn = innings_list[-1]
    innings_num = curr_inn.get("inning_number") or len(innings_list)

    batting_team_id = str(curr_inn.get("batting_team_id", ""))
    batting_team = ""
    for t in data.get("team", []):
        if str(t.get("team_id")) == batting_team_id:
            batting_team = t.get("team_abbreviation") or t.get("team_name", "")
            break

    runs = curr_inn.get("runs", 0)
    wickets = curr_inn.get("wickets")
    overs = curr_inn.get("overs", "0")
    team_prefix = f"{batting_team} " if batting_team else ""
    score_str = (
        f"{team_prefix}{runs}" + (f"/{wickets}" if wickets is not None else "") + f" ({overs})"
    ).strip()

    all_batsmen = curr_inn.get("inningBatsmen", [])
    active_batters: list[BatterState] = []
    dismissed_ids: set[int] = set()

    for b in all_batsmen:
        p_obj = b.get("player") or {}
        pid_val = p_obj.get("objectId")
        if pid_val is None:
            continue
        try:
            pid = int(pid_val)
        except (ValueError, TypeError):
            continue

        is_out = bool(b.get("isOut"))
        short_text = (
            (b.get("dismissalText") or {}).get("short", "").lower()
            if isinstance(b.get("dismissalText"), dict)
            else ""
        )
        is_retired = "retired" in short_text or b.get("dismissalType") == 13
        if is_out or is_retired:
            dismissed_ids.add(pid)

    crease_candidates = []
    for b in all_batsmen:
        if b.get("battedType") != "yes" or b.get("isOut"):
            continue
        short_text = (
            (b.get("dismissalText") or {}).get("short", "").lower()
            if isinstance(b.get("dismissalText"), dict)
            else ""
        )
        if "retired" in short_text or b.get("dismissalType") == 13:
            continue
        crease_candidates.append(b)

    has_current_type = any(b.get("currentType") in (1, 2) for b in crease_candidates)

    if has_current_type:
        for b in crease_candidates:
            ctype = b.get("currentType")
            if ctype in (1, 2):
                p_obj = b.get("player") or {}
                pid = p_obj.get("objectId")
                pname = p_obj.get("name") or p_obj.get("longName") or "Unknown"
                if pid is not None:
                    active_batters.append(
                        BatterState(
                            player_id=int(pid),
                            name=pname,
                            runs=int(b.get("runs") or 0),
                            balls=int(b.get("balls") or 0),
                            on_strike=(ctype == 1),
                        )
                    )
    else:
        for b in crease_candidates[-2:]:
            p_obj = b.get("player") or {}
            pid = p_obj.get("objectId")
            pname = p_obj.get("name") or p_obj.get("longName") or "Unknown"
            if pid is not None:
                active_batters.append(
                    BatterState(
                        player_id=int(pid),
                        name=pname,
                        runs=int(b.get("runs") or 0),
                        balls=int(b.get("balls") or 0),
                        on_strike=False,
                    )
                )

    return LiveBatting(
        match_id=match_id,
        innings=int(innings_num),
        batting_team=batting_team,
        score=score_str,
        batters=tuple(active_batters),
        dismissed_player_ids=frozenset(dismissed_ids),
    )


def _patch_espn_normalize() -> None:
    try:
        import espncricinfo.match as em

        if getattr(em, "_safe_patched", False):
            return

        orig_norm = em._normalize

        def safe_norm(
            next_data: dict[str, Any], match_id: int, series_id: int | None
        ) -> dict[str, Any]:
            try:
                app_data = next_data.get("props", {}).get("appPageProps", {}).get("data", {})
                data = (
                    app_data
                    if ("match" in app_data and "content" in app_data)
                    else app_data.get("data", {})
                )
                content = data.get("content")
                if isinstance(content, dict) and content.get("matchPlayers") is None:
                    content["matchPlayers"] = {}
            except Exception:
                pass
            return orig_norm(next_data, match_id, series_id)

        em._normalize = safe_norm
        em._safe_patched = True
    except Exception:
        pass


class EspnSource:
    def __init__(self, timeout_seconds: int = 10, max_retries: int = 2) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._match_cache: dict[int, dict[str, Any]] = {}
        _patch_espn_normalize()

    def clear_cache(self) -> None:
        self._match_cache.clear()

    def _fetch_match_json(self, match_id: int) -> dict[str, Any]:
        if match_id in self._match_cache:
            return self._match_cache[match_id]

        from espncricinfo.match import Match

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                m = Match(match_id)
                self._match_cache[match_id] = m.json
                return m.json
            except Exception as e:
                last_error = e
                logger.warning(
                    "Fetch match %d failed (attempt %d/%d): %s",
                    match_id,
                    attempt,
                    self.max_retries + 1,
                    e,
                )
                if attempt <= self.max_retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))

        raise SourceError(
            f"Failed to fetch match {match_id} after {self.max_retries + 1} attempts: {last_error}",
            match_id=match_id,
        ) from last_error

    def get_live_match_ids(self) -> list[int]:
        try:
            from datetime import datetime, timedelta, timezone

            from espncricinfo.match import _playwright_fetch

            utc_tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
            local_date = datetime.now().astimezone().date()
            query_date = max(local_date, utc_tomorrow)
            url = f"https://www.espncricinfo.com/live-cricket-match-results?date={query_date:%d-%m-%Y}"

            results_data = _playwright_fetch(url)
            props_data = results_data.get("props", {}).get("appPageProps", {}).get("data", {})
            data_content = props_data.get("data", props_data)
            raw_matches = data_content.get("content", {}).get("matches", [])
            if not raw_matches and isinstance(props_data.get("matches"), list):
                raw_matches = props_data["matches"]

            live_ids: list[int] = []
            for m in raw_matches:
                state = str(m.get("state", "")).upper()
                stage = str(m.get("stage", "")).upper()
                status = str(m.get("status", "")).lower()

                if "not covered" in status:
                    continue

                is_live = (
                    state == "LIVE"
                    or (stage == "RUNNING" and state != "POST" and "result" not in status)
                    or status == "live"
                )

                if is_live:
                    oid = m.get("objectId")
                    if oid is not None:
                        live_ids.append(int(oid))

            return live_ids
        except Exception as e:
            logger.error("Failed to fetch live match list: %s", e)
            raise SourceError(f"Failed to fetch live matches: {e}") from e

    def get_match_info(self, match_id: int) -> MatchInfo:
        data = self._fetch_match_json(match_id)
        try:
            return parse_match_info(data, match_id)
        except Exception as e:
            raise SourceError(
                f"Failed to parse match info for {match_id}: {e}", match_id=match_id
            ) from e

    def get_live_batting(self, match_id: int) -> LiveBatting:
        data = self._fetch_match_json(match_id)
        try:
            return parse_live_batting(data, match_id)
        except Exception as e:
            raise SourceError(
                f"Failed to parse live batting for {match_id}: {e}", match_id=match_id
            ) from e
