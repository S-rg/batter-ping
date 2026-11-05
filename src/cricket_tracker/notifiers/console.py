from __future__ import annotations

import sys

from cricket_tracker.models import BattingAlert, MatchHit


class ConsoleNotifier:
    def notify_lineup(self, hit: MatchHit) -> None:
        players_str = ", ".join(f"{p.name} (id: {p.id})" for p in hit.players)
        print(f"\n[LINEUP ALERT] {hit.title}")
        print(f"  Status:  {hit.status}")
        print(f"  Players: {players_str}")
        print(f"  URL:     {hit.url}\n")

    def notify_batting(self, alert: BattingAlert) -> None:
        batters_desc = []
        for tp, bstate in alert.batters:
            strike_str = " [ON STRIKE]" if bstate.on_strike else ""
            batters_desc.append(f"{tp.name}: {bstate.runs}* ({bstate.balls}){strike_str}")

        print(f"\n[BATTING ALERT] {alert.title} (Innings {alert.innings})")
        print(f"  Score:   {alert.score}")
        print(f"  Batting: {', '.join(batters_desc)}")
        print(f"  URL:     {alert.url}\n")

    def alert(self, message: str) -> None:
        print(f"\n[ADMIN HEALTH ALERT] {message}\n", file=sys.stderr)
