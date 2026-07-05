from __future__ import annotations

import argparse
import json
import logging
import signal

from fluxion.config.settings import Settings
from fluxion.scheduler.autoping import get_autoping_modes, set_autoping_mode
from fluxion.scheduler.daemon import SchedulerDaemon
from fluxion.scheduler.store import ScheduleStore
from fluxion.utils.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fluxion scheduler daemon — fires sub-agent runs on a cron "
        "schedule or when a provider quota window refreshes."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation tick and exit (useful for testing).",
    )
    parser.add_argument(
        "--tick-sec",
        type=int,
        default=0,
        help="Override the poll interval in seconds (default: auto).",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        help="Logging level (debug|info|warning|error).",
    )
    parser.add_argument(
        "--get-autoping",
        action="store_true",
        help="Print managed auto-ping modes as JSON and exit.",
    )
    parser.add_argument(
        "--set-autoping",
        nargs=2,
        metavar=("PROVIDER", "MODE"),
        help="Set a managed auto-ping mode (off|5h|7d|both) and exit.",
    )
    args = parser.parse_args()

    settings = Settings.load()
    settings.validate(require_slack=False)

    if args.get_autoping or args.set_autoping:
        store = ScheduleStore(settings.data_dir)
        try:
            modes = (
                set_autoping_mode(store, *args.set_autoping)
                if args.set_autoping
                else get_autoping_modes(store)
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"providers": modes}, sort_keys=True))
        return

    log_file = settings.data_dir / "logs" / "scheduler.log"
    setup_logging(level=getattr(logging, args.log_level.upper(), logging.INFO), filename=log_file)
    log = logging.getLogger("fluxion.scheduler")

    if not settings.scheduler_enabled:
        log.warning("FLUXION_SCHEDULER_ENABLED is not set to true; scheduler is disabled.")
        return

    tick = args.tick_sec or settings.scheduler_tick_sec or None
    if tick:
        tick = max(15, tick)

    daemon = SchedulerDaemon(settings, tick_sec=tick)

    if args.once:
        daemon.run_once()
        return

    def _handle_signal(signum, frame):  # noqa: ANN001, ARG001
        log.info("received signal %s, shutting down", signum)
        daemon.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    daemon.run_forever()


if __name__ == "__main__":
    main()
