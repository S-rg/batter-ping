from __future__ import annotations

from collections.abc import Iterable

from cricket_tracker.models import TrackedPlayer


class PlayerRegistry:
    def __init__(self, players: Iterable[TrackedPlayer]) -> None:
        self._players: dict[int, TrackedPlayer] = {}
        for player in players:
            if player.id in self._players:
                raise ValueError(f"Duplicate player ID in registry: {player.id}")
            self._players[player.id] = player
        self.ids: frozenset[int] = frozenset(self._players.keys())

    def get(self, player_id: int) -> TrackedPlayer | None:
        return self._players.get(player_id)

    def __contains__(self, player_id: int) -> bool:
        return player_id in self.ids

    def __len__(self) -> int:
        return len(self._players)

    def all(self) -> list[TrackedPlayer]:
        return list(self._players.values())
