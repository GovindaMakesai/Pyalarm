"""JSON persistence for alarms.

Writes are atomic (temp file in the same directory, then ``os.replace``).
Mutations take a short exclusive lock so a scheduler tick cannot overwrite
a snooze saved by a second process. A missing file starts empty. A corrupted
file is moved aside and the store starts fresh.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from pyalarm.alarm import Alarm, AlarmError, postpone

try:
    import msvcrt
except ImportError:  # non-Windows
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:  # non-Unix
    fcntl = None  # type: ignore[assignment]


class StorageError(Exception):
    """Raised when the alarm file cannot be read or written safely."""


def default_store_path() -> Path:
    """Return the per-user alarm file, ``~/.pyalarm/alarms.json``."""
    return Path.home() / ".pyalarm" / "alarms.json"


class AlarmStore:
    """Load and save alarms at an injectable filesystem path."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.warning: Optional[str] = None

    def add(self, time_value: str, label: str = "") -> Alarm:
        """Create the next alarm id and persist it."""

        def mutate(alarms: list[Alarm], next_id: int) -> tuple[Alarm, int, bool]:
            alarm = Alarm(id=next_id, time=time_value, label=label)
            alarms.append(alarm)
            return alarm, next_id + 1, True

        return self.update(mutate)

    def list_alarms(self) -> list[Alarm]:
        """Return a copy of every saved alarm, ordered by id."""

        def mutate(alarms: list[Alarm], next_id: int) -> tuple[list[Alarm], int, bool]:
            copies = [Alarm.from_dict(alarm.to_dict()) for alarm in alarms]
            return copies, next_id, False

        return self.update(mutate)

    def delete(self, alarm_id: int) -> Alarm:
        """Delete one alarm by id.

        Raises:
            AlarmError: No saved alarm uses ``alarm_id``.
        """

        def mutate(alarms: list[Alarm], next_id: int) -> tuple[Alarm, int, bool]:
            alarm = _find(alarms, alarm_id)
            alarms.remove(alarm)
            return alarm, next_id, True

        return self.update(mutate)

    def snooze(self, alarm_id: int, minutes: int, now: Optional[datetime] = None) -> Alarm:
        """Postpone alarm ``alarm_id`` by ``minutes`` from ``now`` and save it.

        ``now`` defaults to the current local time. Tests pass a fixed clock.
        """
        moment = now if now is not None else datetime.now()

        def mutate(alarms: list[Alarm], next_id: int) -> tuple[Alarm, int, bool]:
            alarm = _find(alarms, alarm_id)
            postpone(alarm, moment, minutes)
            return alarm, next_id, True

        return self.update(mutate)

    def update(
        self,
        mutator: Callable[[list[Alarm], int], tuple[Any, int, bool]],
    ) -> Any:
        """Lock, load, run ``mutator``, and save when it reports a change.

        The mutator receives the in-memory alarms and the next id. It returns
        ``(result, next_id, changed)``. It must not call back into the store,
        because this process already holds the file lock.
        """
        with self._file_lock():
            alarms, next_id = self._read()
            result, next_id, changed = mutator(alarms, next_id)
            if changed:
                self._write(alarms, next_id)
            return result

    def _read(self) -> tuple[list[Alarm], int]:
        self.warning = None
        if not self.path.exists():
            return [], 1
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return _parse_payload(payload)
        except OSError as exc:
            raise StorageError(f"Could not read {self.path}: {exc}") from exc
        except (json.JSONDecodeError, AlarmError, StorageError, TypeError, ValueError) as exc:
            backup = self._quarantine()
            self.warning = (
                f"Could not read {self.path} ({exc}). "
                f"Moved it to {backup.name} and started with no alarms."
            )
            return [], 1

    def _quarantine(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        for index in range(100):
            suffix = f"{stamp}.corrupt" if index == 0 else f"{stamp}.{index}.corrupt"
            backup = self.path.with_name(f"{self.path.name}.{suffix}")
            if backup.exists():
                continue
            self.path.replace(backup)
            return backup
        raise StorageError(f"Could not move the unreadable alarm file {self.path}.")

    def _write(self, alarms: list[Alarm], next_id: int) -> None:
        alarms.sort(key=lambda alarm: alarm.id)
        payload = {
            "next_id": next_id,
            "alarms": [alarm.to_dict() for alarm in alarms],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.path)
            except Exception as exc:
                raise StorageError(f"Could not write {self.path}: {exc}") from exc
        finally:
            # os.replace moves the temp file away. If we are interrupted first,
            # delete the partial temp file and leave the previous JSON in place.
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Hold an exclusive lock for one read-modify-write.

        The lock file stays on disk. The lock itself is a byte-range or
        ``flock`` lock released when this context exits, including on error.
        The scheduler does not hold it while sleeping.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            _acquire_lock(fd, self.path)
            yield
        finally:
            _release_lock(fd)
            os.close(fd)


def _parse_payload(payload: Any) -> tuple[list[Alarm], int]:
    if not isinstance(payload, dict):
        raise StorageError("Alarm file must contain a JSON object.")
    raw_alarms = payload.get("alarms", [])
    if not isinstance(raw_alarms, list):
        raise StorageError("The 'alarms' field must be a list.")
    alarms = [Alarm.from_dict(item) for item in raw_alarms]
    ids = [alarm.id for alarm in alarms]
    if len(ids) != len(set(ids)):
        raise StorageError("Alarm file contains duplicate ids.")
    raw_next = payload.get("next_id", 1)
    if raw_next is None:
        raw_next = 1
    if isinstance(raw_next, bool) or not isinstance(raw_next, int):
        raise StorageError("next_id must be an integer.")
    next_id = max(1, raw_next)
    if alarms:
        next_id = max(next_id, max(alarm.id for alarm in alarms) + 1)
    alarms.sort(key=lambda alarm: alarm.id)
    return alarms, next_id


def _find(alarms: list[Alarm], alarm_id: int) -> Alarm:
    for alarm in alarms:
        if alarm.id == alarm_id:
            return alarm
    raise AlarmError(f"No alarm with id {alarm_id}.")


def _acquire_lock(fd: int, path: Path) -> None:
    deadline = time.monotonic() + 5.0
    while True:
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if not _is_lock_contention(exc):
                raise StorageError(f"Could not lock {path}: {exc}") from exc
            if time.monotonic() >= deadline:
                raise StorageError(f"Timed out waiting for the lock on {path}.") from exc
            time.sleep(0.05)


def _is_lock_contention(exc: OSError) -> bool:
    """True when the lock is held by someone else, not when the file is unusable."""
    expected = {errno.EACCES, errno.EAGAIN}
    for name in ("EDEADLK", "EDEADLOCK", "EWOULDBLOCK"):
        value = getattr(errno, name, None)
        if value is not None:
            expected.add(value)
    return exc.errno in expected


def _release_lock(fd: int) -> None:
    try:
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        return
