"""Time utilities."""

from datetime import datetime, timezone


def utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_expired(iso_string: str | None) -> bool:
    """Check if an ISO 8601 timestamp is in the past. None means never expires."""
    if iso_string is None:
        return False
    dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    # Handle naive datetimes from SQLite (assume UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)
