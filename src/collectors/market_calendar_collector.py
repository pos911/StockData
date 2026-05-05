from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as pmc

from src.utils.logger import get_logger

logger = get_logger(__name__)

SEOUL_TZ = ZoneInfo("Asia/Seoul")
MARKET_TIMEZONES = {
    "XKRX": "Asia/Seoul",
    "XNYS": "America/New_York",
    "XNAS": "America/New_York",
}
CALENDAR_CANDIDATES = {
    "XKRX": ("XKRX", "KRX", "Korea", "Seoul"),
    "XNYS": ("XNYS", "NYSE", "New York"),
    "XNAS": ("XNAS", "NASDAQ", "Nasdaq"),
}
MARKET_BY_EXCHANGE = {
    "XKRX": "KRX",
    "XNYS": "US",
    "XNAS": "US",
}


def resolve_market_calendar_name(exchange_code: str) -> str:
    requested = str(exchange_code or "").strip().upper()
    if requested not in CALENDAR_CANDIDATES:
        raise ValueError(f"Unsupported exchange_code for market calendar: {exchange_code}")

    names = list(pmc.get_calendar_names())
    for candidate in CALENDAR_CANDIDATES[requested]:
        if candidate in names:
            logger.info(f"Resolved market calendar name for {requested}: {candidate}")
            return candidate
    for keyword in CALENDAR_CANDIDATES[requested]:
        for name in names:
            if keyword.lower() in name.lower():
                logger.info(f"Resolved market calendar name for {requested}: {name}")
                return name
    raise ValueError(
        f"Unable to resolve a pandas_market_calendars name for {requested}. "
        f"Checked candidates: {', '.join(CALENDAR_CANDIDATES[requested])}."
    )


def resolve_krx_calendar_name() -> str:
    return resolve_market_calendar_name("XKRX")


def _to_market_timestamp(value, timezone_name: str) -> str | None:
    if value is None or value is pd.NaT:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert(ZoneInfo(timezone_name)).isoformat()


def _holiday_name(_calendar, _calendar_date: date) -> str | None:
    return None


def fetch_market_calendar(exchange_code: str, start_date: date, end_date: date) -> list[dict]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    exchange = str(exchange_code or "").strip().upper()
    calendar_name = resolve_market_calendar_name(exchange)
    timezone_name = MARKET_TIMEZONES.get(exchange, "UTC")
    market_name = MARKET_BY_EXCHANGE.get(exchange, exchange)
    calendar = pmc.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=start_date, end_date=end_date)
    schedule_map = {
        pd.Timestamp(index).date(): row
        for index, row in schedule.iterrows()
    }

    collected_at = datetime.now(SEOUL_TZ).isoformat()
    rows: list[dict] = []
    current = start_date
    while current <= end_date:
        schedule_row = schedule_map.get(current)
        if schedule_row is not None:
            is_open = True
            reason = "trading_day"
            open_time = _to_market_timestamp(schedule_row.get("market_open"), timezone_name)
            close_time = _to_market_timestamp(schedule_row.get("market_close"), timezone_name)
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
                "exchange_code": exchange,
                "market": market_name,
                "is_open": is_open,
                "open_time": open_time,
                "close_time": close_time,
                "timezone": timezone_name,
                "holiday_name": holiday_name,
                "reason": reason,
                "source": "pandas_market_calendars",
                "calendar_version": getattr(pmc, "__version__", None),
                "collected_at": collected_at,
                "updated_at": collected_at,
            }
        )
        current += timedelta(days=1)

    logger.info(
        f"Fetched market calendar rows: exchange_code={exchange} calendar_name={calendar_name} "
        f"start_date={start_date} end_date={end_date} total_days={len(rows)}"
    )
    return rows


def fetch_krx_calendar(start_date: date, end_date: date) -> list[dict]:
    return fetch_market_calendar("XKRX", start_date, end_date)


def fetch_us_calendar(start_date: date, end_date: date) -> list[dict]:
    return fetch_market_calendar("XNYS", start_date, end_date)
