from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)

EXCHANGE_FALLBACK_NAMES = {
    "XKRX": "KRX",
    "XNYS": "US",
    "XNAS": "US",
}


@lru_cache(maxsize=32)
def _warn_once(message: str) -> None:
    logger.warning(message)


def _iso(value: date | str) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _exchange(exchange_code: str | None) -> str:
    return str(exchange_code or "XKRX").strip().upper()


def _weekday_is_open(target_date: date) -> bool:
    return target_date.weekday() < 5


def _fallback_warning(exchange_code: str) -> None:
    _warn_once(
        f"market_trading_calendar unavailable for {exchange_code}; "
        "CALENDAR_FALLBACK_USED with simple weekday logic. Temporary exchange holidays may be missed."
    )


def _fetch_calendar_rows(
    loader: SupabaseLoader,
    start_date: date | str,
    end_date: date | str,
    exchange_code: str,
) -> list[dict]:
    exchange = _exchange(exchange_code)
    try:
        rows = loader.fetch_all(
            "market_trading_calendar",
            "calendar_date",
            _iso(start_date),
            _iso(end_date),
            order_col="calendar_date",
            desc=False,
        )
        return [row for row in rows if _exchange(row.get("exchange_code")) == exchange]
    except Exception:
        _fallback_warning(exchange)
        return []


def _fetch_calendar_row(loader: SupabaseLoader, target_date: date, exchange_code: str) -> dict | None:
    rows = _fetch_calendar_rows(loader, target_date, target_date, exchange_code)
    return rows[0] if rows else None


def is_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> bool:
    row = _fetch_calendar_row(loader, target_date, exchange_code)
    if row is None:
        return _weekday_is_open(target_date)
    return bool(row.get("is_open"))


def is_market_open(loader: SupabaseLoader, target_date: date, exchange_code: str) -> bool:
    return is_trading_day(loader, target_date, exchange_code)


def should_skip_market_job(
    loader: SupabaseLoader,
    target_date: date,
    exchange_code: str,
    job_name: str,
) -> tuple[bool, str]:
    exchange = _exchange(exchange_code)
    row = _fetch_calendar_row(loader, target_date, exchange)
    if row is None:
        if _weekday_is_open(target_date):
            return False, "MARKET_OPEN"
        fallback_market = EXCHANGE_FALLBACK_NAMES.get(exchange, exchange)
        logger.warning(
            f"{job_name}: market_trading_calendar missing for {exchange}; "
            f"weekday fallback marks {target_date} as closed for {fallback_market}."
        )
        return True, f"MARKET_CLOSED: {exchange} {target_date.isoformat()} weekday_fallback"

    if row.get("is_open"):
        return False, "MARKET_OPEN"
    reason = row.get("reason") or "holiday"
    return True, f"MARKET_CLOSED: {exchange} {target_date.isoformat()} {reason}"


def get_previous_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> date | None:
    current = target_date - timedelta(days=1)
    for _ in range(366):
        if is_trading_day(loader, current, exchange_code):
            return current
        current -= timedelta(days=1)
    return None


def get_market_previous_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str) -> date | None:
    return get_previous_trading_day(loader, target_date, exchange_code)


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


def get_market_latest_trading_day_on_or_before(
    loader: SupabaseLoader,
    target_date: date,
    exchange_code: str,
) -> date | None:
    return get_latest_trading_day_on_or_before(loader, target_date, exchange_code)


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
