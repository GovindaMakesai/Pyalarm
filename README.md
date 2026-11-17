# PyAlarm

PyAlarm is a local command-line alarm clock. It stores daily alarms in a JSON file and can run a foreground scheduler that prints a notification when an alarm's minute arrives.

There is no web UI, database, or third-party package. The project uses the Python standard library only.

## Features

- Add a daily alarm in 24-hour `HH:MM` format, with an optional label.
- List saved alarms, including the next time each one will fire.
- Delete an alarm by its numeric id.
- Snooze an alarm by postponing its next scheduled occurrence.
- Run a foreground scheduler that checks the local clock once a second.
- Print a notification when an alarm fires, with the terminal bell as an optional sound.
- Keep alarms in a local JSON file. A missing file starts empty. A corrupted file is moved aside.
- Reject invalid times, unknown ids, and invalid snooze minutes without deleting other alarms.
- Stop the scheduler cleanly on Ctrl+C.

Alarms repeat every day. An alarm fires at most once for a given minute. If the scheduler is not running during that minute, the alarm is not delivered late.

## Snooze

Snooze postpones the next scheduled occurrence of an alarm. It is not an interactive snooze button while the scheduler is running. You run it as its own command, and the new time is written to the JSON file. A scheduler that is already running reloads the file on its next one-second tick and sees the change.

The next scheduled occurrence is:

1. The pending snooze time, if a snooze is still in the future or inside its minute and has not fired yet.
2. Otherwise today's `HH:MM`, if that minute has not ended.
3. Otherwise tomorrow's `HH:MM`.

`snooze --minutes N` stores that occurrence plus N minutes. N is an integer from 1 through 1440. The daily `HH:MM` does not change. After the postponed minute fires, the stored snooze is cleared and the alarm returns to its daily time.

Examples, for an alarm set to `07:30`:

- Snooze 5 minutes at 06:00. The next fire time becomes 07:35 the same day. The original 07:30 minute does not also fire.
- Snooze 5 minutes at 08:00. Today's 07:30 minute has ended, so the next fire time becomes 07:35 the next day.
- Snooze again while a snooze is still pending. The extra minutes are added to that pending time.

## Python version

Python 3.10 or newer.

This project was verified with Python 3.13.12, started with `py -3` on Windows. On that machine, the `python` command is Python 2.7.18 and cannot run this code. Use `py -3` when `python --version` reports Python 2.

## Installation

No packages to install. From the project directory:

```text
py -3 --version
py -3 main.py --help
```

If `python --version` already reports Python 3.10 or newer, use `python` anywhere this README shows `py -3`.

Alarms are stored at `~/.pyalarm/alarms.json` unless you pass `--file`.

## Usage

```text
py -3 main.py set 07:30 --label "Morning"
py -3 main.py set 18:45 --label "Gym"
py -3 main.py list
py -3 main.py snooze 1 --minutes 5
py -3 main.py delete 2
py -3 main.py start --bell
```

Press Ctrl+C to stop `start`. Omit `--bell` to print the notification without the terminal bell.

Use another file when you do not want to touch the default one:

```text
py -3 main.py --file alarms.json set 07:30 --label "Morning"
```

`alarms.json` in this directory is gitignored.

## Commands and expected behavior

| Command | What it does |
| --- | --- |
| `set HH:MM [--label TEXT]` | Saves a new daily alarm and prints its id. `HH:MM` must be zero-padded, from `00:00` through `23:59`. |
| `list` | Prints every alarm, the next fire time, and whether that time is the daily slot or a snooze. Prints `No alarms saved.` when the file is empty or missing. |
| `delete ID` | Removes that alarm. Ids are not reused. An unknown id prints an error and leaves the file unchanged. |
| `snooze ID --minutes N` | Postpones the next scheduled occurrence by N minutes and saves it. See [Snooze](#snooze). |
| `start [--bell]` | Polls the local clock every second. Prints a banner during a due minute. `--bell` also writes the terminal bell character. Ctrl+C prints `Scheduler stopped.` and exits 0. |
| `--file PATH` | Reads and writes that JSON file instead of `~/.pyalarm/alarms.json`. |

Invalid input and missing ids exit with status 1 and a one-line `Error:` message on stderr. A corrupted JSON file is renamed to `alarms.json.<timestamp>.corrupt` (next to the original). The command then continues with no alarms and prints a warning. The previous bytes stay in the backup file.

## Architecture

```text
main.py                 CLI only: argparse, printing, exit codes
pyalarm/alarm.py        Time parsing, next occurrence, snooze rules
pyalarm/storage.py      JSON load/save, atomic replace, file lock
pyalarm/scheduler.py    One-second poll, trigger-once bookkeeping
tests/                  unittest, with temp directories and a fake clock
```

`main.py` does not calculate alarm times. `scheduler.py` asks `alarm.py` whether an alarm is due, then asks `storage.py` to save `last_triggered` before printing. The storage path and the clock are arguments, so tests do not wait for the wall clock and do not use the home directory.

The on-disk document looks like this:

```json
{
  "next_id": 2,
  "alarms": [
    {
      "id": 1,
      "time": "07:30",
      "label": "Morning",
      "snooze_until": "2026-11-18T07:35",
      "last_triggered": null
    }
  ]
}
```

`time` is the daily clock time. `snooze_until` is the one postponed occurrence, or `null`. `last_triggered` is the minute key that already fired, so the same minute is not printed again after a restart.

## Engineering Decisions

**Standard-library Python.** argparse, datetime, json, pathlib, and unittest cover the whole program. A third-party scheduler or CLI framework would add install steps and hide the small amount of logic this tool actually has.

**JSON persistence.** An alarm list is a few records. A human-readable file is enough, and it can be inspected during a demo. Writes go to a temporary file in the same directory and then `os.replace`, so a crash mid-write does not leave a half-written JSON document. A missing file means there are no alarms. A file that is not valid JSON, or whose records are not valid alarms, is moved to a `.corrupt` backup instead of being overwritten in place.

**A one-second polling scheduler.** Alarms are minute-resolution. Sleeping one second is simple, keeps the process in the foreground, and is easy to stop with Ctrl+C. The clock and the sleep function are injectable, so tests can fire an alarm without waiting. The scheduler reloads the file on every tick, so a snooze saved from a second terminal is picked up without a restart. A thread-per-alarm design would not make minute alarms more correct.

A short exclusive lock is held only around each read-modify-write, not while sleeping. That stops a scheduler tick from saving stale data over a snooze that was just written. The lock is released when the process exits, including on Ctrl+C.

## Testing

From the project directory, with Python 3:

```text
py -3 -m unittest discover -s tests -v
```

The suite covers valid and invalid `HH:MM` values, alarm creation, listing, deletion, JSON reload, corrupt-file recovery, snooze arithmetic, the next-occurrence rules, and scheduler firing. Scheduler tests pass a fake clock into `Scheduler.tick`, so they do not wait for a real minute. `Scheduler.run` is tested by a sleeper that raises `KeyboardInterrupt` on the first one-second wait.

No network access and no extra packages are required.

## Known limitations

- Times are naive local times. Daylight-saving gaps and repeated hours are not handled specially.
- The scheduler must be running during the alarm minute. It does not fire a missed minute when it starts later.
- Delivery can be up to about one second after the minute starts.
- Snooze changes stored data. The running scheduler does not ask you to snooze.
- `--bell` only helps when the terminal actually plays the BEL character.
- This is a single-user CLI, not a background service. Two writers are serialized, but the tool is not a multi-user server.
- A past `snooze_until` is ignored. It stays in the file until the next snooze or until that postponed minute fires.
- `python` on the development machine used for this project is Python 2.7. The commands above use `py -3` for that reason.

## Example terminal output

Captured with `py -3` and `--file` pointed at a temporary directory, on 17 November 2026 in the afternoon. Next-fire dates follow the local clock, so a morning alarm listed after 07:30 is scheduled for the next day.

```text
> py -3 main.py --file alarms.json set 07:30 --label "Morning"
Added alarm 1 at 07:30 (Morning).

> py -3 main.py --file alarms.json set 18:45 --label "Gym"
Added alarm 2 at 18:45 (Gym).

> py -3 main.py --file alarms.json list
  ID  Time   Next              Schedule  Label
   1  07:30  2026-11-18 07:30  daily     Morning
   2  18:45  2026-11-17 18:45  daily     Gym

> py -3 main.py --file alarms.json snooze 1 --minutes 5
Snoozed alarm 1 until 2026-11-18 07:35. Daily time remains 07:30.

> py -3 main.py --file alarms.json list
  ID  Time   Next              Schedule  Label
   1  07:30  2026-11-18 07:35  snoozed   Morning
   2  18:45  2026-11-17 18:45  daily     Gym

> py -3 main.py --file alarms.json delete 99
Error: No alarm with id 99.

> py -3 main.py --file alarms.json set 25:00
Error: Invalid time '25:00'. Use 24-hour HH:MM from 00:00 through 23:59.
```

A scheduler started for a `15:40` alarm, with `--bell`, printed the startup lines, rang the terminal bell, and saved `"last_triggered": "2026-11-17T15:40"`. The banner below is the current ASCII form of that notification:

```text
Scheduler running. Press Ctrl+C to stop.
Using alarm file C:\Users\Reyan\AppData\Local\Temp\...\live.json
================================================
ALARM 1 - Live
Scheduled: 2026-11-17 15:40
Now:       2026-11-17 15:40:00
================================================
```
