from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def get_kst_timezone() -> timezone:
    return timezone(timedelta(hours=9))


def get_current_kst() -> datetime:
    """Return the current time in Asia/Seoul."""
    return datetime.now(get_kst_timezone())


def get_current_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_kst_target_date(now_utc: datetime | None = None, mode: str = "market") -> date:
    """Return the KST target date derived from the runner's UTC clock."""
    del mode
    current_utc = now_utc or get_current_utc()
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    return current_utc.astimezone(get_kst_timezone()).date()


def parse_date_string(date_str: str, format_str: str = "%Y%m%d") -> date:
    """Parse YYYYMMDD or YYYY-MM-DD into a date."""
    cleaned = str(date_str).strip()
    if "-" in cleaned and format_str == "%Y%m%d":
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    return datetime.strptime(cleaned, format_str).date()


def generate_available_at_for_eod(base_date: date) -> datetime:
    """Return a conservative KST EOD availability timestamp for a market day."""
    return datetime(
        base_date.year,
        base_date.month,
        base_date.day,
        16,
        0,
        0,
        tzinfo=get_kst_timezone(),
    )
