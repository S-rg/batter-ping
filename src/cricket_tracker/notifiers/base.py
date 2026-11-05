from __future__ import annotations

from typing import Protocol

from cricket_tracker.models import BattingAlert, MatchHit


class Notifier(Protocol):
    def notify_lineup(self, hit: MatchHit) -> None: ...

    def notify_batting(self, alert: BattingAlert) -> None: ...

    def alert(self, message: str) -> None: ...
