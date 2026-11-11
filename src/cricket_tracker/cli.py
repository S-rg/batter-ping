from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import time

from cricket_tracker.config import ConfigError, load_config
from cricket_tracker.notifiers import build_notifier
from cricket_tracker.players import PlayerRegistry
from cricket_tracker.source import EspnSource
from cricket_tracker.state import StateStore
from cricket_tracker.tracker import Tracker

logger = logging.getLogger("cricket_tracker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cricket-tracker",
        description="Cron-friendly live cricket match player tracker",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without modifying state or sending webhooks (uses ConsoleNotifier)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed logging output",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete the state file and lock file, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        config = load_config(args.config, validate_notifier=not (args.dry_run or args.reset_state))
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        return 2
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        return 2

    state = StateStore(
        state_file=config.state_file,
        squads_recheck_seconds=config.squads_recheck_seconds,
        xi_recheck_seconds=config.xi_recheck_seconds,
        evict_after_seconds=config.evict_after_seconds,
    )

    if args.reset_state:
        state_file = config.state_file
        lock_file = state_file.with_name(f"{state_file.name}.lock")
        deleted = False

        with state.lock() as acquired:
            if not acquired:
                logger.error(
                    "Cannot reset state: another process is currently running and holds the lock."
                )
                return 1

            if state_file.exists():
                state_file.unlink()
                deleted = True
                logger.info("Deleted state file: %s", state_file)

            for cfile in state_file.parent.glob(f"{state_file.name}.corrupt-*"):
                cfile.unlink()
                logger.info("Deleted corrupt state file: %s", cfile)

        if lock_file.exists():
            with contextlib.suppress(OSError):
                lock_file.unlink()
                deleted = True
                logger.info("Deleted lock file: %s", lock_file)

        if not deleted:
            logger.info("No state or lock file existed at %s", state_file)
        return 0

    registry = PlayerRegistry(config.players)
    source = EspnSource(
        timeout_seconds=config.request_timeout_seconds,
        max_tabs=config.browser_max_tabs,
        fetch_budget_seconds=config.fetch_budget_seconds,
        javascript=config.browser_javascript,
    )
    notifier = build_notifier(config, dry_run=args.dry_run)

    with state.lock() as acquired:
        if not acquired:
            logger.info("Previous run still active (lock held). Skipping this execution.")
            return 0

        state.load()

        t_start = time.time()
        tracker = Tracker(
            source=source,
            state=state,
            registry=registry,
            notifier=notifier,
            notify_lineup=config.notify.lineup,
            notify_batting=config.notify.batting,
            max_workers=config.max_workers,
        )

        result = None
        duration = 0.0
        try:
            result = tracker.run()
            duration = time.time() - t_start

            health_alert_sent = state.is_health_alert_sent()
            failed_runs = state.data.get("health", {}).get("consecutive_failed_runs", 0)

            if not result.live_list_ok:
                if failed_runs >= config.alert_after_failed_runs and not health_alert_sent:
                    try:
                        last_err = result.errors[-1] if result.errors else "Unknown"
                        notifier.alert(
                            f"Cricket Tracker scraper failed for {failed_runs} consecutive runs! "
                            f"Last error: {last_err}"
                        )
                        state.set_health_alert_sent(True)
                    except Exception as alert_err:
                        logger.error("Failed to send health alert: %s", alert_err)
            else:
                if health_alert_sent:
                    try:
                        notifier.alert(
                            "Cricket Tracker live match fetching has recovered successfully."
                        )
                    except Exception:
                        pass
                    state.set_health_alert_sent(False)
        finally:
            source.close()
            if not args.dry_run:
                try:
                    state.save()
                except Exception as save_err:
                    logger.error("Failed to save state file: %s", save_err)

        if result is None:
            return 1

        summary_line = (
            f"live={result.live_count} lineups={result.lineups_fetched} "
            f"hot={result.hot_matches} lineup_alerts={result.lineup_alerts} "
            f"batting_alerts={result.batting_alerts} errors={len(result.errors)} "
            f"duration={duration:.2f}s"
        )
        logger.info(summary_line)

        if not result.live_list_ok:
            return 1
        return 0


def resolve_players_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve ESPNcricinfo player IDs to names")
    parser.add_argument("player_ids", metavar="ID", type=int, nargs="+", help="ESPN player IDs")
    args = parser.parse_args(argv)

    from espncricinfo.player import Player

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
    sys.exit(main())
