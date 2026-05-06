from __future__ import annotations

import argparse
from datetime import date, timedelta

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.market_data_quality import is_valid_price_row
from src.utils.time_utils import get_current_kst, parse_date_string
from src.utils.trading_calendar import get_previous_trading_day, is_market_open

logger = get_logger(__name__)


def _count_valid_stock_rows(loader: SupabaseLoader, target_date: date) -> int:
    rows = loader.fetch_all(
        "normalized_stock_prices_daily",
        "base_date",
        target_date.isoformat(),
        target_date.isoformat(),
    )
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    market_map = {row.get("symbol"): row.get("market") for row in master_rows if row.get("symbol")}
    return sum(
        1
        for row in rows
        if market_map.get(row.get("symbol")) in {"KOSPI", "KOSDAQ"}
        and is_valid_price_row(row, market_is_open=True)
    )


def _count_kis_detail_valid_rows(loader: SupabaseLoader, target_date: date) -> tuple[int, int]:
    target_date_str = target_date.isoformat()
    if not hasattr(loader, "client"):
        return 0, 0
    raw_rows = (
        loader.client.table("raw_stock_prices_daily")
        .select("symbol")
        .eq("base_date", target_date_str)
        .eq("source", "KIS_DETAIL")
        .execute()
        .data
        or []
    )
    detail_symbols = {row.get("symbol") for row in raw_rows if row.get("symbol")}
    if not detail_symbols:
        return 0, 0
    normalized_rows = loader.fetch_all(
        "normalized_stock_prices_daily",
        "base_date",
        target_date_str,
        target_date_str,
    )
    valid_rows = sum(
        1
        for row in normalized_rows
        if row.get("symbol") in detail_symbols and is_valid_price_row(row, market_is_open=True)
    )
    return len(detail_symbols), valid_rows


def run_feature_pipeline(target_date: date) -> tuple[str, int]:
    logger.info(f"Starting Feature Pipeline wrapper for {target_date}...")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    xkrx_open = is_market_open(loader, target_date, "XKRX")
    valid_rows = _count_valid_stock_rows(loader, target_date)
    detail_universe_count, detail_valid_rows = _count_kis_detail_valid_rows(loader, target_date)
    logger.info(
        f"feature_pipeline_target_date={target_date} xkrx_is_open={xkrx_open} "
        f"valid_stock_rows={valid_rows} detail_universe_count={detail_universe_count} detail_valid_rows={detail_valid_rows}"
    )

    minimum_required = max(10, detail_universe_count // 5) if detail_universe_count else 100
    effective_valid_rows = detail_valid_rows if detail_universe_count else valid_rows
    if xkrx_open and effective_valid_rows < minimum_required:
        previous_trading_day = get_previous_trading_day(loader, target_date, "XKRX")
        previous_valid_rows = _count_valid_stock_rows(loader, previous_trading_day) if previous_trading_day else 0
        logger.warning(
            f"Insufficient valid stock rows for feature generation on {target_date}: "
            f"valid_rows={effective_valid_rows}, minimum_required={minimum_required}, previous_trading_day={previous_trading_day}, "
            f"previous_valid_rows={previous_valid_rows}"
        )
        loader.insert_log(
            "daily_feature_generator",
            target_date.strftime("%Y-%m-%d"),
            "SKIPPED_INSUFFICIENT_PRICE_DATA",
            0,
            (
                f"target_valid_rows={effective_valid_rows}; minimum_required={minimum_required}; previous_trading_day={previous_trading_day}; "
                f"previous_valid_rows={previous_valid_rows}"
            ),
        )
        return "SKIPPED_INSUFFICIENT_PRICE_DATA", 0

    from src.features.generate_features import run_job as generate_features_job

    processed = generate_features_job(target_date)
    status = "SUCCESS" if processed > 0 else "WARN"
    logger.info(f"Feature Pipeline wrapper finished. status={status}, processed={processed}")
    return status, processed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()

    target_dt = get_current_kst().date()
    if args.date:
        target_dt = parse_date_string(args.date)

    run_feature_pipeline(target_dt)
