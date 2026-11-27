"""Time helpers.

Rows store naive UTC datetimes so SQLite and PostgreSQL behave identically.
Time-of-day logic uses the machine's local time, so a demo at 9 am on the
clock on the wall is a morning peak in the simulated city too.
"""

from __future__ import annotations

from datetime import UTC, datetime


def local_hour_and_weekday(epoch_seconds: float) -> tuple[float, int]:
    moment = datetime.fromtimestamp(epoch_seconds)
    return moment.hour + moment.minute / 60.0, moment.weekday()


def to_naive_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is not None:
        moment = to_naive_utc(moment)
    return moment.isoformat() + "Z"
