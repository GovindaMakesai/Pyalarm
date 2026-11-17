"""Command-line interface for PyAlarm.

Business rules live in ``pyalarm``. This module only parses arguments,
prints results, and exits with a status code.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from pyalarm.alarm import AlarmError, minute_key, next_occurrence
from pyalarm.scheduler import Scheduler
from pyalarm.storage import AlarmStore, StorageError, default_store_path


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Show argument defaults and preserve example line breaks."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser used by the CLI and by ``--help``."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "PyAlarm is a local command-line alarm clock. "
            "Alarms repeat every day at a local 24-hour time. "
            "Snooze postpones the next scheduled occurrence; "
            "it is not a prompt inside the scheduler."
        ),
        epilog=textwrap.dedent(
            """\
            examples:
              python main.py set 07:30 --label "Morning"
              python main.py list
              python main.py delete 1
              python main.py snooze 1 --minutes 5
              python main.py start --bell
            """
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=default_store_path(),
        help="JSON file used to store alarms",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    set_alarm = commands.add_parser(
        "set",
        help="Add a daily alarm",
        description="Add an alarm that repeats every day at a local HH:MM time.",
        formatter_class=HelpFormatter,
    )
    set_alarm.add_argument("time", help="Alarm time in 24-hour HH:MM format")
    set_alarm.add_argument("--label", default="", help="Optional alarm label")

    commands.add_parser(
        "list",
        help="List saved alarms",
        description="List saved alarms and the next time each one will fire.",
        formatter_class=HelpFormatter,
    )

    delete_alarm = commands.add_parser(
        "delete",
        help="Delete an alarm by id",
        description="Delete one saved alarm. Ids are shown by the list command.",
        formatter_class=HelpFormatter,
    )
    delete_alarm.add_argument("id", type=int, help="Id of the alarm to delete")

    snooze_alarm = commands.add_parser(
        "snooze",
        help="Postpone the next occurrence of an alarm",
        description=(
            "Add minutes to the next scheduled occurrence and save that time. "
            "Does not change the daily HH:MM and does not prompt while the scheduler runs."
        ),
        formatter_class=HelpFormatter,
    )
    snooze_alarm.add_argument("id", type=int, help="Id of the alarm to postpone")
    snooze_alarm.add_argument(
        "--minutes",
        type=int,
        required=True,
        help="Minutes to add to the next scheduled occurrence (1-1440)",
    )

    start = commands.add_parser(
        "start",
        help="Run the scheduler in the foreground",
        description=(
            "Poll local time once a second and print a notification when an alarm minute begins. "
            "Press Ctrl+C to stop. An alarm minute that passes while the scheduler is stopped "
            "is not delivered late."
        ),
        formatter_class=HelpFormatter,
    )
    start.add_argument(
        "--bell",
        action="store_true",
        help="Ring the terminal bell when an alarm fires",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one PyAlarm command. Return a process exit code."""
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    store = AlarmStore(args.file)
    try:
        if args.command == "set":
            alarm = store.add(args.time, args.label)
            _emit_warning(store)
            label = f" ({alarm.label})" if alarm.label else ""
            print(f"Added alarm {alarm.id} at {alarm.time}{label}.", flush=True)
        elif args.command == "list":
            alarms = store.list_alarms()
            _emit_warning(store)
            _print_alarms(alarms)
        elif args.command == "delete":
            removed = store.delete(args.id)
            _emit_warning(store)
            label = f" ({removed.label})" if removed.label else ""
            print(f"Deleted alarm {removed.id} at {removed.time}{label}.", flush=True)
        elif args.command == "snooze":
            alarm = store.snooze(args.id, args.minutes)
            _emit_warning(store)
            when = (alarm.snooze_until or "").replace("T", " ")
            print(
                f"Snoozed alarm {alarm.id} until {when}. Daily time remains {alarm.time}.",
                flush=True,
            )
        elif args.command == "start":
            Scheduler(store).run(bell=args.bell)
        else:
            parser.error(f"Unknown command {args.command!r}.")
    except (AlarmError, StorageError) as exc:
        _emit_warning(store)
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


def _print_alarms(alarms: list) -> None:
    if not alarms:
        print("No alarms saved.", flush=True)
        return
    now = datetime.now()
    print(f"{'ID':>4}  {'Time':<5}  {'Next':<16}  {'Schedule':<8}  Label", flush=True)
    for alarm in alarms:
        upcoming = next_occurrence(alarm, now)
        schedule = "snoozed" if alarm.snooze_until == minute_key(upcoming) else "daily"
        label = alarm.label or "-"
        print(
            f"{alarm.id:>4}  {alarm.time:<5}  "
            f"{upcoming.strftime('%Y-%m-%d %H:%M'):<16}  {schedule:<8}  {label}",
            flush=True,
        )


def _configure_stdio() -> None:
    """Do not let an unprintable label crash the scheduler after an alarm is saved."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            continue


def _emit_warning(store: AlarmStore) -> None:
    if store.warning:
        print(f"Warning: {store.warning}", file=sys.stderr, flush=True)
        store.warning = None


if __name__ == "__main__":
    raise SystemExit(main())
