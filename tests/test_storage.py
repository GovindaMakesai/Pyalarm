"""Tests for JSON persistence, listing, deletion, and corruption recovery."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pyalarm.alarm import AlarmError
from pyalarm.storage import AlarmStore


class AlarmStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "alarms.json"
        self.store = AlarmStore(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_lists_nothing(self) -> None:
        self.assertEqual(self.store.list_alarms(), [])
        self.assertIsNone(self.store.warning)
        self.assertFalse(self.path.exists())

    def test_add_list_and_reload(self) -> None:
        first = self.store.add("07:30", "  Morning ")
        second = self.store.add("18:45", "Gym")
        self.assertEqual((first.id, first.time, first.label), (1, "07:30", "Morning"))
        self.assertEqual(second.id, 2)

        listed = self.store.list_alarms()
        self.assertEqual([(alarm.id, alarm.label) for alarm in listed], [(1, "Morning"), (2, "Gym")])

        reloaded = AlarmStore(self.path).list_alarms()
        self.assertEqual([alarm.time for alarm in reloaded], ["07:30", "18:45"])
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["next_id"], 3)
        self.assertEqual(payload["alarms"][0]["label"], "Morning")

    def test_delete_removes_one_alarm_and_does_not_reuse_ids(self) -> None:
        first = self.store.add("07:30", "Morning")
        self.store.add("08:00", "Standup")
        removed = self.store.delete(first.id)
        self.assertEqual(removed.id, 1)
        remaining = self.store.list_alarms()
        self.assertEqual([alarm.id for alarm in remaining], [2])
        created = self.store.add("09:15", "Deep work")
        self.assertEqual(created.id, 3)

    def test_delete_missing_id_leaves_the_file_unchanged(self) -> None:
        self.store.add("07:30", "Morning")
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(AlarmError) as caught:
            self.store.delete(99)
        self.assertIn("No alarm with id 99", str(caught.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_snooze_persists_and_reload_keeps_daily_time(self) -> None:
        alarm = self.store.add("07:30", "Morning")
        updated = self.store.snooze(alarm.id, 5, now=datetime(2026, 11, 17, 6, 0))
        self.assertEqual(updated.snooze_until, "2026-11-17T07:35")
        self.assertEqual(updated.time, "07:30")
        reloaded = AlarmStore(self.path).list_alarms()[0]
        self.assertEqual(reloaded.snooze_until, "2026-11-17T07:35")
        self.assertEqual(reloaded.time, "07:30")

    def test_snooze_missing_id_raises(self) -> None:
        self.store.add("07:30")
        with self.assertRaises(AlarmError) as caught:
            self.store.snooze(5, 5, now=datetime(2026, 11, 17, 6, 0))
        self.assertIn("No alarm with id 5", str(caught.exception))
        self.assertEqual(len(self.store.list_alarms()), 1)

    def test_invalid_time_is_rejected_and_does_not_create_a_file(self) -> None:
        with self.assertRaises(AlarmError):
            self.store.add("7:30", "Morning")
        self.assertFalse(self.path.exists())

    def test_corrupt_files_are_quarantined(self) -> None:
        samples = ["{bad", "[]", '{"alarms": [{"id": 1}]}']
        for index, contents in enumerate(samples):
            with self.subTest(contents=contents):
                path = Path(self.tmp.name) / f"bad-{index}.json"
                path.write_text(contents, encoding="utf-8")
                store = AlarmStore(path)
                self.assertEqual(store.list_alarms(), [])
                self.assertIsNotNone(store.warning)
                self.assertIn("started with no alarms", store.warning or "")
                backups = list(Path(self.tmp.name).glob(f"bad-{index}.json.*.corrupt"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), contents)
                self.assertFalse(path.exists())
                created = store.add("07:30", "Recovered")
                self.assertEqual(created.id, 1)
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
