import datetime

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
IST_TZ = datetime.timezone(IST_OFFSET, name="IST")

def get_ist_now() -> datetime.datetime:
    """Returns the current aware datetime in IST."""
    return datetime.datetime.now(IST_TZ)

def get_ist_now_iso() -> str:
    """Returns the current IST datetime as an ISO 8601 string.
    Example: 2026-03-09T07:15:30+05:30
    """
    # Use timespec='seconds' if we want to avoid microseconds, but standard isoformat() is good too.
    return get_ist_now().replace(microsecond=0).isoformat()

def parse_iso_to_ist(iso_string: str) -> datetime.datetime:
    """Parses an ISO string and returns an IST aware datetime."""
    # Replace 'Z' with explicit UTC offset for fromisoformat compatibility in earlier Python versions
    dt = datetime.datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
    return dt.astimezone(IST_TZ)
