from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger
from src.utils.symbols import normalize_symbol_value

logger = get_logger(__name__)


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
) -> dict[str, Any]:
    target_date_str = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)[:10]
    target_dt = date.fromisoformat(target_date_str)
    start_dt = target_dt - timedelta(days=max(lookback_days * 3, 14))

    date_rows = (
        loader.client.table("normalized_stock_prices_daily")
        .select("base_date")
        .gte("base_date", start_dt.isoformat())
        .lte("base_date", target_date_str)
        .order("base_date", desc=True)
        .execute()
        .data
        or []
    )
    candidate_dates = []
    seen = set()
    for row in date_rows:
        base_date = str(row.get("base_date") or "")[:10]
        if not base_date or base_date in seen:
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
        rows = (
            loader.client.table("normalized_stock_prices_daily")
            .select("symbol, close_price, volume, trading_value")
            .eq("base_date", candidate_date)
            .execute()
            .data
            or []
        )
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
