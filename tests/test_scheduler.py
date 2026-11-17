"""Scheduler tests use an injected clock so they do not wait on wall time."""

from __future__ import annotations

import io
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pyalarm.scheduler import Scheduler
from pyalarm.storage import AlarmStore


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "alarms.json"
        self.store = AlarmStore(self.path)
        self.notes: list[tuple[int, str, bool]] = []
        self.output = io.StringIO()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _scheduler(self, clock) -> Scheduler:
        def notify(alarm, occurrence, now, bell) -> None:
            del now
            self.notes.append((alarm.id, occurrence.strftime("%Y-%m-%dT%H:%M"), bell))

        return Scheduler(
            self.store,
            clock=clock,
            sleeper=lambda _seconds: None,
            notifier=notify,
            output=self.output,
        )

    def test_fires_once_for_the_matching_minute(self) -> None:
        alarm = self.store.add("07:30", "Wake")
        current = {"now": datetime(2026, 11, 17, 7, 30, 8)}
        scheduler = self._scheduler(lambda: current["now"])

        self.assertEqual(scheduler.tick(bell=True), [alarm.id])
        self.assertEqual(self.notes, [(alarm.id, "2026-11-17T07:30", True)])
        self.assertEqual(scheduler.tick(bell=True), [])
        self.assertEqual(len(self.notes), 1)
        saved = self.store.list_alarms()[0]
        self.assertEqual(saved.last_triggered, "2026-11-17T07:30")

    def test_same_time_fires_again_on_the_next_day(self) -> None:
        alarm = self.store.add("07:30")
        current = {"now": datetime(2026, 11, 17, 7, 30, 0)}
        scheduler = self._scheduler(lambda: current["now"])
        self.assertEqual(scheduler.tick(), [alarm.id])
        current["now"] = datetime(2026, 11, 18, 7, 30, 0)
        self.assertEqual(scheduler.tick(), [alarm.id])
        self.assertEqual(len(self.notes), 2)

    def test_does_not_fire_early_or_after_the_window(self) -> None:
        self.store.add("07:30", "Wake")
        current = {"now": datetime(2026, 11, 17, 7, 29, 59)}
        scheduler = self._scheduler(lambda: current["now"])
        self.assertEqual(scheduler.tick(), [])
        current["now"] = datetime(2026, 11, 17, 7, 31, 0)
        self.assertEqual(scheduler.tick(), [])
        self.assertEqual(self.notes, [])

    def test_only_the_matching_alarm_fires(self) -> None:
        self.store.add("07:30", "Morning")
        later = self.store.add("08:00", "Gym")
        scheduler = self._scheduler(lambda: datetime(2026, 11, 17, 8, 0, 1))
        self.assertEqual(scheduler.tick(), [later.id])
        self.assertEqual([note[0] for note in self.notes], [later.id])

    def test_snoozed_alarm_fires_only_at_the_postponed_minute(self) -> None:
        alarm = self.store.add("07:30", "Morning")
        self.store.snooze(alarm.id, 5, now=datetime(2026, 11, 17, 7, 0))
        current = {"now": datetime(2026, 11, 17, 7, 30, 10)}
        scheduler = self._scheduler(lambda: current["now"])

        self.assertEqual(scheduler.tick(), [])
        current["now"] = datetime(2026, 11, 17, 7, 35, 10)
        self.assertEqual(scheduler.tick(bell=False), [alarm.id])
        saved = self.store.list_alarms()[0]
        self.assertIsNone(saved.snooze_until)
        self.assertEqual(saved.last_triggered, "2026-11-17T07:35")
        self.assertEqual(saved.time, "07:30")
        self.assertEqual(scheduler.tick(), [])

    def test_run_stops_cleanly_on_keyboard_interrupt(self) -> None:
        seen: list[float] = []

        def sleeper(seconds: float) -> None:
            seen.append(seconds)
            raise KeyboardInterrupt

        scheduler = Scheduler(
            self.store,
            clock=lambda: datetime(2026, 11, 17, 1, 0, 0),
            sleeper=sleeper,
            notifier=lambda *args: None,
            output=self.output,
        )
        scheduler.run(bell=False)
        self.assertEqual(seen, [1.0])
        text = self.output.getvalue()
        self.assertIn("Scheduler running", text)
        self.assertIn("Scheduler stopped.", text)


if __name__ == "__main__":
    unittest.main()
