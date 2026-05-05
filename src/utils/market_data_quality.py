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


def _is_valid_price_row(row: dict[str, Any]) -> bool:
    return (
        row.get("close_price") not in (None, "")
        and row.get("volume") not in (None, "")
        and row.get("trading_value") not in (None, "")
    )


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
        valid_rows = [row for row in rows if _is_valid_price_row(row)]
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
