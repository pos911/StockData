from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as pmc

from src.utils.logger import get_logger

logger = get_logger(__name__)

SEOUL_TZ = ZoneInfo("Asia/Seoul")
CALENDAR_KEYWORDS = ("XKRX", "KRX", "Korea", "Seoul")


def resolve_krx_calendar_name() -> str:
    names = list(pmc.get_calendar_names())
    if "XKRX" in names:
        logger.info("Resolved KRX market calendar name: XKRX")
        return "XKRX"
    for keyword in CALENDAR_KEYWORDS:
        for name in names:
            if keyword.lower() in name.lower():
                logger.info(f"Resolved KRX market calendar name: {name}")
                return name
    raise ValueError(
        "Unable to resolve a KRX-compatible calendar name from pandas_market_calendars. "
        "Checked keywords: XKRX, KRX, Korea, Seoul."
    )


def _to_seoul_timestamp(value) -> str | None:
    if value is None or value is pd.NaT:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert(SEOUL_TZ).isoformat()


def _holiday_name(_calendar, _calendar_date: date) -> str | None:
    return None


def fetch_krx_calendar(start_date: date, end_date: date) -> list[dict]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    calendar_name = resolve_krx_calendar_name()
    calendar = pmc.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=start_date, end_date=end_date)
    schedule_map = {
        pd.Timestamp(index).date(): row
        for index, row in schedule.iterrows()
    }

    rows: list[dict] = []
    current = start_date
    while current <= end_date:
        schedule_row = schedule_map.get(current)
        if schedule_row is not None:
            is_open = True
            reason = "trading_day"
            open_time = _to_seoul_timestamp(schedule_row.get("market_open"))
            close_time = _to_seoul_timestamp(schedule_row.get("market_close"))
            holiday_name = None
        else:
            is_open = False
            reason = "weekend" if current.weekday() >= 5 else "holiday"
            open_time = None
            close_time = None
            holiday_name = None if reason == "weekend" else _holiday_name(calendar, current)

        rows.append(
            {
                "calendar_date": current.isoformat(),
                "exchange_code": calendar_name,
                "market": "KRX",
                "is_open": is_open,
                "open_time": open_time,
                "close_time": close_time,
                "timezone": "Asia/Seoul",
                "holiday_name": holiday_name,
                "reason": reason,
                "source": "pandas_market_calendars",
                "calendar_version": getattr(pmc, "__version__", None),
                "collected_at": datetime.now(SEOUL_TZ).isoformat(),
                "updated_at": datetime.now(SEOUL_TZ).isoformat(),
            }
        )
        current += timedelta(days=1)

    logger.info(
        f"Fetched KRX market calendar rows: calendar_name={calendar_name} "
        f"start_date={start_date} end_date={end_date} total_days={len(rows)}"
    )
    return rows
