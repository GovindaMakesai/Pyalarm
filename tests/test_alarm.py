"""Tests for time parsing, occurrence calculation, and snooze."""

from __future__ import annotations

import unittest
from datetime import datetime

from pyalarm.alarm import (
    Alarm,
    AlarmError,
    due_occurrence,
    minute_key,
    next_daily_slot,
    next_occurrence,
    parse_time,
    postpone,
)


class ParseTimeTests(unittest.TestCase):
    def test_accepts_valid_24_hour_times(self) -> None:
        self.assertEqual(parse_time("00:00"), (0, 0))
        self.assertEqual(parse_time("07:30"), (7, 30))
        self.assertEqual(parse_time("23:59"), (23, 59))
        self.assertEqual(parse_time(" 07:05 "), (7, 5))

    def test_rejects_invalid_times(self) -> None:
        for value in ("24:00", "7:30", "12:60", "12:30:00", "noon", "", "07:30am", "25:00"):
            with self.subTest(value=value):
                with self.assertRaises(AlarmError):
                    parse_time(value)


class AlarmCreationTests(unittest.TestCase):
    def test_creation_normalizes_time_and_label(self) -> None:
        alarm = Alarm(id=1, time="07:30", label="  Morning  run ")
        self.assertEqual(alarm.time, "07:30")
        self.assertEqual(alarm.label, "Morning run")
        self.assertIsNone(alarm.snooze_until)
        self.assertIsNone(alarm.last_triggered)

    def test_creation_rejects_bad_time(self) -> None:
        with self.assertRaises(AlarmError):
            Alarm(id=1, time="24:00")


class OccurrenceTests(unittest.TestCase):
    def test_next_daily_slot_is_today_until_the_minute_ends(self) -> None:
        alarm = Alarm(1, "07:30")
        self.assertEqual(
            next_daily_slot(alarm, datetime(2026, 11, 17, 7, 0, 0)),
            datetime(2026, 11, 17, 7, 30),
        )
        self.assertEqual(
            next_daily_slot(alarm, datetime(2026, 11, 17, 7, 30, 59)),
            datetime(2026, 11, 17, 7, 30),
        )
        self.assertEqual(
            next_daily_slot(alarm, datetime(2026, 11, 17, 7, 31, 0)),
            datetime(2026, 11, 18, 7, 30),
        )

    def test_midnight_alarm_window(self) -> None:
        alarm = Alarm(1, "00:00")
        during = datetime(2026, 11, 17, 0, 0, 45)
        after = datetime(2026, 11, 17, 0, 1, 0)
        self.assertEqual(due_occurrence(alarm, during), datetime(2026, 11, 17, 0, 0))
        self.assertIsNone(due_occurrence(alarm, after))
        self.assertEqual(next_occurrence(alarm, after), datetime(2026, 11, 18, 0, 0))

    def test_next_occurrence_skips_a_minute_that_already_fired(self) -> None:
        alarm = Alarm(1, "07:30", last_triggered="2026-11-17T07:30")
        now = datetime(2026, 11, 17, 7, 30, 10)
        self.assertEqual(next_occurrence(alarm, now), datetime(2026, 11, 18, 7, 30))
        self.assertIsNone(due_occurrence(alarm, now))

    def test_due_only_inside_the_minute_and_only_once(self) -> None:
        alarm = Alarm(1, "07:30")
        self.assertIsNone(due_occurrence(alarm, datetime(2026, 11, 17, 7, 29, 59)))
        due = due_occurrence(alarm, datetime(2026, 11, 17, 7, 30, 0))
        self.assertEqual(due, datetime(2026, 11, 17, 7, 30))
        alarm.last_triggered = minute_key(due) if due is not None else None
        self.assertIsNone(due_occurrence(alarm, datetime(2026, 11, 17, 7, 30, 40)))
        self.assertIsNone(due_occurrence(alarm, datetime(2026, 11, 17, 7, 31, 0)))

    def test_expired_snooze_falls_back_to_the_daily_schedule(self) -> None:
        alarm = Alarm(1, "07:30", snooze_until="2026-11-17T07:35")
        now = datetime(2026, 11, 17, 8, 0)
        self.assertEqual(next_occurrence(alarm, now), datetime(2026, 11, 18, 7, 30))
        self.assertIsNone(due_occurrence(alarm, now))


class SnoozeTests(unittest.TestCase):
    def test_postpone_before_the_alarm_moves_today(self) -> None:
        alarm = Alarm(1, "07:30", label="Morning")
        when = postpone(alarm, datetime(2026, 11, 17, 6, 0), 5)
        self.assertEqual(when, datetime(2026, 11, 17, 7, 35))
        self.assertEqual(alarm.snooze_until, "2026-11-17T07:35")
        self.assertEqual(alarm.time, "07:30")
        during_original = datetime(2026, 11, 17, 7, 30, 10)
        self.assertIsNone(due_occurrence(alarm, during_original))
        self.assertEqual(
            due_occurrence(alarm, datetime(2026, 11, 17, 7, 35, 1)),
            datetime(2026, 11, 17, 7, 35),
        )

    def test_postpone_after_the_minute_moves_tomorrow(self) -> None:
        alarm = Alarm(1, "07:30")
        when = postpone(alarm, datetime(2026, 11, 17, 8, 0), 10)
        self.assertEqual(when, datetime(2026, 11, 18, 7, 40))

    def test_postpone_compounds_an_active_snooze(self) -> None:
        alarm = Alarm(1, "07:30", snooze_until="2026-11-17T07:35")
        when = postpone(alarm, datetime(2026, 11, 17, 7, 0), 5)
        self.assertEqual(when, datetime(2026, 11, 17, 7, 40))
        self.assertEqual(alarm.time, "07:30")

    def test_postpone_accepts_one_day_and_rejects_invalid_minutes(self) -> None:
        alarm = Alarm(1, "07:30")
        when = postpone(alarm, datetime(2026, 11, 17, 7, 0), 1440)
        self.assertEqual(when, datetime(2026, 11, 18, 7, 30))
        for minutes in (0, -5, 1441, True):
            with self.subTest(minutes=minutes):
                with self.assertRaises(AlarmError):
                    postpone(Alarm(1, "07:30"), datetime(2026, 11, 17, 7, 0), minutes)


if __name__ == "__main__":
    unittest.main()
