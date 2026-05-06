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


def get_market_calendar_row(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> dict | None:
    return _fetch_calendar_row(loader, target_date, exchange_code)


def get_market_calendar_diagnostic(
    loader: SupabaseLoader,
    target_date: date,
    exchange_code: str = "XKRX",
) -> dict:
    row = _fetch_calendar_row(loader, target_date, exchange_code)
    if row is None:
        return {
            "exchange_code": _exchange(exchange_code),
            "calendar_date": target_date.isoformat(),
            "is_open": _weekday_is_open(target_date),
            "reason": "weekday_fallback_open" if _weekday_is_open(target_date) else "weekday_fallback_closed",
            "source": None,
            "holiday_name": None,
            "open_time": None,
            "close_time": None,
            "calendar_fallback_used": True,
        }
    return {
        "exchange_code": _exchange(exchange_code),
        "calendar_date": str(row.get("calendar_date"))[:10],
        "is_open": bool(row.get("is_open")),
        "reason": row.get("reason"),
        "source": row.get("source"),
        "holiday_name": row.get("holiday_name"),
        "open_time": row.get("open_time"),
        "close_time": row.get("close_time"),
        "calendar_fallback_used": False,
    }


def is_trading_day(loader: SupabaseLoader, target_date: date, exchange_code: str = "XKRX") -> bool:
    return bool(get_market_calendar_diagnostic(loader, target_date, exchange_code).get("is_open"))


def is_market_open(loader: SupabaseLoader, target_date: date, exchange_code: str) -> bool:
    return is_trading_day(loader, target_date, exchange_code)


def should_skip_market_job(
    loader: SupabaseLoader,
    target_date: date,
    exchange_code: str,
    job_name: str,
) -> tuple[bool, str]:
    exchange = _exchange(exchange_code)
    diagnostic = get_market_calendar_diagnostic(loader, target_date, exchange)
    if diagnostic["calendar_fallback_used"]:
        if diagnostic["is_open"]:
            return False, "MARKET_OPEN"
        fallback_market = EXCHANGE_FALLBACK_NAMES.get(exchange, exchange)
        logger.warning(
            f"{job_name}: market_trading_calendar missing for {exchange}; "
            f"weekday fallback marks {target_date} as closed for {fallback_market}."
        )
        return True, f"MARKET_CLOSED: {exchange} {target_date.isoformat()} weekday_fallback"

    if diagnostic["is_open"]:
        return (
            False,
            f"MARKET_OPEN: {exchange} {target_date.isoformat()} "
            f"{diagnostic.get('reason')} source={diagnostic.get('source')}",
        )
    reason = diagnostic.get("reason") or "holiday"
    return (
        True,
        f"MARKET_CLOSED: {exchange} {target_date.isoformat()} "
        f"{reason} source={diagnostic.get('source')}",
    )


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
