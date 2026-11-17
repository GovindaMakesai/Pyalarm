"""Foreground one-second scheduler.

The scheduler reloads the alarm file on every tick, so a snooze saved by
another ``python main.py snooze`` process is picked up without a restart.
It does not ask for input. Snooze remains a separate command.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Callable, Optional, TextIO

from pyalarm.alarm import Alarm, due_occurrence, minute_key
from pyalarm.storage import AlarmStore

POLL_INTERVAL = 1.0

Notifier = Callable[[Alarm, datetime, datetime, bool], None]


def default_notify(alarm: Alarm, occurrence: datetime, now: datetime, bell: bool) -> None:
    """Print a notification. Ring the terminal bell when ``bell`` is true."""
    label = f" - {alarm.label}" if alarm.label else ""
    banner = "=" * 48
    print(banner, flush=True)
    print(f"ALARM {alarm.id}{label}", flush=True)
    print(f"Scheduled: {occurrence.strftime('%Y-%m-%d %H:%M')}", flush=True)
    print(f"Now:       {now.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(banner, flush=True)
    if bell:
        print("\a", end="", flush=True)


class Scheduler:
    """Poll local time once a second and fire due alarms at most once a minute."""

    def __init__(
        self,
        store: AlarmStore,
        clock: Optional[Callable[[], datetime]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        notifier: Optional[Notifier] = None,
        output: Optional[TextIO] = None,
    ) -> None:
        self.store = store
        self.clock = clock or datetime.now
        self.sleeper = sleeper or time.sleep
        self.notifier = notifier or default_notify
        self.output = output or sys.stdout
        self.interval = POLL_INTERVAL

    def tick(self, bell: bool = False) -> list[int]:
        """Check every alarm once. Return the ids that fired.

        Trigger state is saved before the notification is printed, so a crash
        or a second poll in the same minute does not repeat the notification.
        """
        now = self._now()
        pending: list[tuple[Alarm, datetime]] = []

        def mark(alarms: list[Alarm], next_id: int) -> tuple[None, int, bool]:
            for alarm in alarms:
                occurrence = due_occurrence(alarm, now)
                if occurrence is None:
                    continue
                alarm.last_triggered = minute_key(occurrence)
                if alarm.snooze_until == minute_key(occurrence):
                    alarm.snooze_until = None
                pending.append((alarm, occurrence))
            return None, next_id, bool(pending)

        self.store.update(mark)
        fired: list[int] = []
        for alarm, occurrence in pending:
            self.notifier(alarm, occurrence, now, bell)
            fired.append(alarm.id)
        if self.store.warning:
            print(f"Warning: {self.store.warning}", file=sys.stderr, flush=True)
            self.store.warning = None
        return fired

    def run(self, bell: bool = False) -> None:
        """Poll until the user presses Ctrl+C. Exit cleanly on KeyboardInterrupt."""
        print("Scheduler running. Press Ctrl+C to stop.", file=self.output, flush=True)
        print(f"Using alarm file {self.store.path}", file=self.output, flush=True)
        try:
            while True:
                self.tick(bell=bell)
                self.sleeper(self.interval)
        except KeyboardInterrupt:
            print("\nScheduler stopped.", file=self.output, flush=True)

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is not None and current.utcoffset() is not None:
            return current.astimezone().replace(tzinfo=None)
        return current
