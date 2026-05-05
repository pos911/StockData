from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Iterable

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=8)
def _warn_once(message: str) -> None:
    logger.warning(message)


def _iso(value: date | str) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _fetch_calendar_rows(
    loader: SupabaseLoader,
    start_date: date | str,
    end_date: date | str,
    exchange_code: str,
) -> list[dict]:
    try:
        rows = loader.fetch_all(
            "market_trading_calendar",
            "calendar_date",
            _iso(start_date),
            _iso(end_date),
            order_col="calendar_date",
            desc=False,
        )
        return [row for row in rows if row.get("exchange_code") == exchange_code]
    except Exception as exc:
        _warn_once(
            f"market_trading_calendar unavailable ({exc}); calendar table missing, weekday fallback used"
        )
        return []


def _weekday_is_open(target_date: date) -> bool:
    return target_date.weekday() < 5


def is_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> bool:
    rows = _fetch_calendar_rows(loader, target_date, target_date, exchange_code)
    if not rows:
        return _weekday_is_open(target_date)
    return bool(rows[0].get("is_open"))


def get_previous_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> date | None:
    current = target_date - timedelta(days=1)
    for _ in range(366):
        if is_trading_day(loader, current, exchange_code):
            return current
        current -= timedelta(days=1)
    return None


def get_next_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> date | None:
    current = target_date + timedelta(days=1)
    for _ in range(366):
        if is_trading_day(loader, current, exchange_code):
            return current
        current += timedelta(days=1)
    return None


def get_latest_trading_day_on_or_before(
    loader: SupabaseLoader,
    target_date: date,
    exchange_code: str = "XKRX",
) -> date | None:
    current = target_date
    for _ in range(366):
        if is_trading_day(loader, current, exchange_code):
            return current
        current -= timedelta(days=1)
    return None


def get_trading_days_between(
    loader: SupabaseLoader,
    start_date: date,
    end_date: date,
    exchange_code: str = "XKRX",
) -> list[date]:
    rows = _fetch_calendar_rows(loader, start_date, end_date, exchange_code)
    if rows:
        return [
            _date(row["calendar_date"])
            for row in rows
            if row.get("is_open")
        ]
    trading_days = []
    current = start_date
    while current <= end_date:
        if _weekday_is_open(current):
            trading_days.append(current)
        current += timedelta(days=1)
    return trading_days
