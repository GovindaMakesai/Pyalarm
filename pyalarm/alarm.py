"""Alarm time parsing and occurrence calculations.

Alarms repeat every day at a local HH:MM time. A snooze stores a one-off
replacement for the next occurrence and does not change the daily time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MINUTE_FMT = "%Y-%m-%dT%H:%M"
MAX_SNOOZE_MINUTES = 24 * 60


class AlarmError(ValueError):
    """Raised when alarm input is invalid or an alarm id does not exist."""


def parse_time(value: str) -> tuple[int, int]:
    """Parse a 24-hour ``HH:MM`` string into ``(hour, minute)``.

    Hours and minutes must be zero-padded. ``7:30`` is rejected; ``07:30``
    is accepted. Surrounding whitespace is ignored.
    """
    if not isinstance(value, str):
        raise AlarmError("Time must be a string in HH:MM format.")
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        raise AlarmError(
            f"Invalid time {value!r}. Use 24-hour HH:MM from 00:00 through 23:59."
        )
    return int(match.group(1)), int(match.group(2))


def minute_key(moment: datetime) -> str:
    """Return a stable local-minute key, ``YYYY-MM-DDTHH:MM``."""
    return moment.strftime(_MINUTE_FMT)


def parse_minute_key(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored minute key. Return None when it is empty or invalid."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _MINUTE_FMT)
    except ValueError:
        return None


@dataclass
class Alarm:
    """A daily local alarm persisted in the JSON store."""

    id: int
    time: str
    label: str = ""
    snooze_until: Optional[str] = None
    last_triggered: Optional[str] = None

    def __post_init__(self) -> None:
        hour, minute = parse_time(self.time)
        self.time = f"{hour:02d}:{minute:02d}"
        self.label = " ".join((self.label or "").split())
        if self.snooze_until is not None and parse_minute_key(self.snooze_until) is None:
            raise AlarmError(f"Invalid snooze timestamp {self.snooze_until!r}.")
        if self.last_triggered is not None and parse_minute_key(self.last_triggered) is None:
            raise AlarmError(
                f"Invalid last-triggered timestamp {self.last_triggered!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this alarm for JSON storage."""
        return {
            "id": self.id,
            "time": self.time,
            "label": self.label,
            "snooze_until": self.snooze_until,
            "last_triggered": self.last_triggered,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Alarm:
        """Build an alarm from a JSON object.

        Raises:
            AlarmError: The record is missing fields or has the wrong types.
        """
        if not isinstance(data, dict):
            raise AlarmError("Alarm record must be a JSON object.")
        try:
            alarm_id = data["id"]
            clock_time = data["time"]
        except KeyError as exc:
            raise AlarmError(f"Alarm record is missing {exc.args[0]!r}.") from exc
        if isinstance(alarm_id, bool) or not isinstance(alarm_id, int):
            raise AlarmError(f"Alarm id must be an integer, got {alarm_id!r}.")
        label = data.get("label") or ""
        if not isinstance(label, str):
            raise AlarmError("Alarm label must be a string.")
        snooze_until = data.get("snooze_until")
        last_triggered = data.get("last_triggered")
        if snooze_until is not None and not isinstance(snooze_until, str):
            raise AlarmError("snooze_until must be a string or null.")
        if last_triggered is not None and not isinstance(last_triggered, str):
            raise AlarmError("last_triggered must be a string or null.")
        return cls(
            id=alarm_id,
            time=str(clock_time),
            label=label,
            snooze_until=snooze_until,
            last_triggered=last_triggered,
        )


def next_daily_slot(alarm: Alarm, now: datetime) -> datetime:
    """Return today's HH:MM if that minute has not ended, otherwise tomorrow's.

    The result is naive local time with seconds and microseconds cleared.
    The minute window is ``[HH:MM:00, HH:MM:59]``. At ``HH:MM+1`` the next
    slot is the following day.
    """
    hour, minute = parse_time(alarm.time)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= candidate + timedelta(minutes=1):
        return candidate + timedelta(days=1)
    return candidate


def _active_snooze(alarm: Alarm, now: datetime) -> Optional[datetime]:
    """Return a postponed minute that is still pending at ``now``."""
    snooze = parse_minute_key(alarm.snooze_until)
    if snooze is None:
        return None
    if alarm.last_triggered == minute_key(snooze):
        return None
    if now >= snooze + timedelta(minutes=1):
        return None
    return snooze


def next_occurrence(alarm: Alarm, now: datetime) -> datetime:
    """Return the next local time this alarm should fire.

    A pending snooze replaces the daily time until that minute fires or
    passes. If the daily minute is already underway and has been triggered,
    the following day is returned so the same minute is not shown again.
    """
    snooze = _active_snooze(alarm, now)
    if snooze is not None:
        return snooze
    slot = next_daily_slot(alarm, now)
    if alarm.last_triggered == minute_key(slot) and now >= slot:
        return slot + timedelta(days=1)
    return slot


def due_occurrence(alarm: Alarm, now: datetime) -> Optional[datetime]:
    """Return the occurrence minute when it should fire at ``now``.

    The alarm is due only while ``now`` is inside that minute and the
    occurrence has not already been recorded in ``last_triggered``.
    """
    occurrence = next_occurrence(alarm, now)
    if alarm.last_triggered == minute_key(occurrence):
        return None
    if occurrence <= now < occurrence + timedelta(minutes=1):
        return occurrence
    return None


def postpone(alarm: Alarm, now: datetime, minutes: int) -> datetime:
    """Postpone the next scheduled occurrence by ``minutes``.

    The base time is an active snooze when one is still pending. Otherwise
    it is today's HH:MM if that minute has not ended, else tomorrow's HH:MM.
    The daily clock time is left unchanged. The new instant is stored on
    ``alarm.snooze_until`` and returned.

    Raises:
        AlarmError: ``minutes`` is not a positive integer, or it is over 24 hours.
    """
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise AlarmError("Snooze minutes must be a positive integer.")
    if minutes > MAX_SNOOZE_MINUTES:
        raise AlarmError(
            f"Snooze minutes cannot exceed {MAX_SNOOZE_MINUTES} (24 hours)."
        )
    snooze = _active_snooze(alarm, now)
    base = snooze if snooze is not None else next_daily_slot(alarm, now)
    postponed = base + timedelta(minutes=minutes)
    alarm.snooze_until = minute_key(postponed)
    return postponed
