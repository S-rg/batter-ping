from __future__ import annotations

import sys

try:
    from cricket_tracker.cli import resolve_players_main
except ImportError:
    import argparse

    from espncricinfo.player import Player

    def resolve_players_main(argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="Resolve ESPNcricinfo player IDs to names")
        parser.add_argument("player_ids", metavar="ID", type=int, nargs="+", help="ESPN player IDs")
        args = parser.parse_args(argv)

        print("players:")
        for pid in args.player_ids:
            try:
                p = Player(pid)
                name = p.full_name or p.name
                print(f"  - {{ id: {pid}, name: {name} }}")
            except Exception as e:
                print(f"Failed to resolve {pid}: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(resolve_players_main())
