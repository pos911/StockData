from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger
from src.utils.symbols import normalize_symbol_value
from src.utils.trading_calendar import get_latest_trading_day_on_or_before, get_trading_days_between

logger = get_logger(__name__)


def _fetch_rows(
    loader: SupabaseLoader,
    table_name: str,
    date_col: str,
    start_date: str,
    end_date: str,
    order_col: str | None = None,
    desc: bool = True,
):
    fetch_all = getattr(loader, "fetch_all", None)
    if callable(fetch_all):
        try:
            return fetch_all(
                table_name,
                date_col,
                start_date,
                end_date,
                order_col=order_col,
                desc=desc,
            )
        except TypeError:
            return fetch_all(table_name, date_col, start_date, end_date)

    query = loader.client.table(table_name).select("*").gte(date_col, start_date).lte(date_col, end_date)
    if order_col:
        query = query.order(order_col, desc=desc)
    result = query.execute()
    return result.data or []


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_trading_day_strings(
    loader: SupabaseLoader,
    start_date: date,
    end_date: date,
    exchange_code: str = "XKRX",
) -> set[str]:
    return {
        trading_day.isoformat()
        for trading_day in get_trading_days_between(loader, start_date, end_date, exchange_code=exchange_code)
    }


def is_valid_price_row(
    row: dict[str, Any],
    market_is_open: bool | None = None,
    require_source: bool = False,
) -> bool:
    close_price = _to_float(row.get("close_price"))
    volume = _to_float(row.get("volume"))
    trading_value = _to_float(row.get("trading_value"))
    if require_source and not row.get("source"):
        return False
    return (
        close_price is not None
        and close_price > 0
        and volume is not None
        and volume > 0
        and trading_value is not None
        and trading_value > 0
    )


def scan_invalid_price_rows(
    loader: SupabaseLoader,
    target_date: date,
    lookback_days: int = 180,
) -> dict[str, Any]:
    start_date = target_date - timedelta(days=lookback_days)
    rows = _fetch_rows(
        loader,
        "normalized_stock_prices_daily",
        "base_date",
        start_date.isoformat(),
        target_date.isoformat(),
        order_col="base_date",
        desc=False,
    )
    master_map = _load_master_market_map(loader)
    trading_days = load_trading_day_strings(loader, start_date, target_date, exchange_code="XKRX")
    market_rows = []
    for row in rows:
        symbol = normalize_symbol_value(row.get("symbol"))
        market, _asset_type = master_map.get(symbol, (None, None))
        if market in {"KOSPI", "KOSDAQ", "ETF", "ETN"}:
            copied = dict(row)
            copied["symbol"] = symbol
            copied["market"] = market
            market_rows.append(copied)

    non_trading_rows = [
        row for row in market_rows if str(row.get("base_date") or "")[:10] not in trading_days
    ]
    zero_close_rows = [
        row for row in market_rows if (_to_float(row.get("close_price")) or 0) <= 0
    ]
    null_source_rows = [row for row in market_rows if not row.get("source")]

    row_lookup = {
        (row["symbol"], str(row.get("base_date") or "")[:10]): row
        for row in market_rows
    }
    duplicate_weekend_carry_rows = []
    for row in non_trading_rows:
        base_date_str = str(row.get("base_date") or "")[:10]
        try:
            base_dt = date.fromisoformat(base_date_str)
        except ValueError:
            continue
        previous_trading_day = get_latest_trading_day_on_or_before(
            loader,
            base_dt - timedelta(days=1),
            exchange_code="XKRX",
        )
        if previous_trading_day is None:
            continue
        prev_row = row_lookup.get((row["symbol"], previous_trading_day.isoformat()))
        if not prev_row:
            continue
        if (
            _to_float(prev_row.get("close_price")) == _to_float(row.get("close_price"))
            and _to_float(prev_row.get("volume")) == _to_float(row.get("volume"))
            and _to_float(prev_row.get("trading_value")) == _to_float(row.get("trading_value"))
        ):
            duplicate_weekend_carry_rows.append(row)

    recent_cutoff = target_date - timedelta(days=30)
    recent_invalid_rows = [
        row
        for row in market_rows
        if any(
            row in bucket
            for bucket in (non_trading_rows, zero_close_rows, null_source_rows)
        )
        and date.fromisoformat(str(row.get("base_date") or "")[:10]) >= recent_cutoff
    ]

    status = "SUCCESS"
    if recent_invalid_rows:
        status = "FAIL_INVALID_PRICE_ROWS"
    elif non_trading_rows or zero_close_rows or null_source_rows:
        status = "WARN_INVALID_PRICE_ROWS"

    return {
        "non_trading_day_price_rows": len(non_trading_rows),
        "zero_close_rows": len(zero_close_rows),
        "null_source_rows": len(null_source_rows),
        "duplicate_weekend_carry_rows": len(duplicate_weekend_carry_rows),
        "sample_non_trading_rows": non_trading_rows[:10],
        "sample_zero_close_rows": zero_close_rows[:10],
        "sample_null_source_rows": null_source_rows[:10],
        "sample_duplicate_weekend_carry_rows": duplicate_weekend_carry_rows[:10],
        "status": status,
    }


def _load_master_market_map(loader: SupabaseLoader) -> dict[str, tuple[str | None, str | None]]:
    rows = loader.client.table("stocks_master").select("symbol, market, asset_type").execute().data or []
    return {
        normalize_symbol_value(row.get("symbol")): (row.get("market"), row.get("asset_type"))
        for row in rows
        if row.get("symbol")
    }


def get_latest_valid_price_date(
    loader: SupabaseLoader,
    target_date: date | str,
    lookback_days: int = 10,
    min_valid_rows: int = 100,
    exchange_code: str = "XKRX",
) -> dict[str, Any]:
    target_date_str = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)[:10]
    target_dt = date.fromisoformat(target_date_str)
    latest_trading_day = get_latest_trading_day_on_or_before(loader, target_dt, exchange_code=exchange_code)
    if latest_trading_day is not None:
        target_dt = latest_trading_day
        target_date_str = latest_trading_day.isoformat()
    start_dt = target_dt - timedelta(days=max(lookback_days * 3, 14))

    date_rows = _fetch_rows(
        loader,
        "normalized_stock_prices_daily",
        "base_date",
        start_dt.isoformat(),
        target_date_str,
        order_col="base_date",
        desc=True,
    )
    candidate_dates = []
    seen = set()
    trading_day_set = {
        trading_day.isoformat()
        for trading_day in get_trading_days_between(loader, start_dt, target_dt, exchange_code=exchange_code)
    }
    for row in date_rows:
        base_date = str(row.get("base_date") or "")[:10]
        if not base_date or base_date in seen:
            continue
        if trading_day_set and base_date not in trading_day_set:
            continue
        seen.add(base_date)
        candidate_dates.append(base_date)
        if len(candidate_dates) >= lookback_days:
            break

    master_map = _load_master_market_map(loader)
    rejected_dates = []
    selected = {
        "selected_price_base_date": None,
        "valid_rows_count": 0,
        "market_counts": {},
        "rejected_dates": rejected_dates,
    }

    for candidate_date in candidate_dates:
        rows = _fetch_rows(loader, "normalized_stock_prices_daily", "base_date", candidate_date, candidate_date)
        valid_rows = [row for row in rows if is_valid_price_row(row, market_is_open=True)]
        market_counter = Counter()
        for row in valid_rows:
            market, _asset_type = master_map.get(normalize_symbol_value(row.get("symbol")), (None, None))
            if market:
                market_counter[str(market)] += 1
        if len(valid_rows) >= min_valid_rows:
            selected = {
                "selected_price_base_date": candidate_date,
                "valid_rows_count": len(valid_rows),
                "market_counts": dict(market_counter),
                "rejected_dates": rejected_dates,
            }
            logger.info(
                f"selected_price_base_date={candidate_date}, valid_rows_count={len(valid_rows)}, "
                f"market_counts={dict(market_counter)}, rejected_dates={rejected_dates}"
            )
            return selected
        rejected_dates.append(
            {
                "base_date": candidate_date,
                "valid_rows_count": len(valid_rows),
                "market_counts": dict(market_counter),
            }
        )

    logger.warning(
        f"No valid price date met threshold. selected_price_base_date=None, "
        f"valid_rows_count=0, rejected_dates={rejected_dates}"
    )
    return selected
