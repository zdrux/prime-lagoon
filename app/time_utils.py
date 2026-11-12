"""UTC boundaries for snapshots; legacy timestamps without offsets are UTC."""
from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_snapshot_time(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
