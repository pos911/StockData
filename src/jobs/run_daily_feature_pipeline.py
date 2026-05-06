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


def run_feature_pipeline(target_date: date) -> tuple[str, int]:
    logger.info(f"Starting Feature Pipeline wrapper for {target_date}...")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    xkrx_open = is_market_open(loader, target_date, "XKRX")
    valid_rows = _count_valid_stock_rows(loader, target_date)
    logger.info(f"feature_pipeline_target_date={target_date} xkrx_is_open={xkrx_open} valid_stock_rows={valid_rows}")

    if xkrx_open and valid_rows < 100:
        previous_trading_day = get_previous_trading_day(loader, target_date, "XKRX")
        previous_valid_rows = _count_valid_stock_rows(loader, previous_trading_day) if previous_trading_day else 0
        logger.warning(
            f"Insufficient valid stock rows for feature generation on {target_date}: "
            f"valid_rows={valid_rows}, previous_trading_day={previous_trading_day}, "
            f"previous_valid_rows={previous_valid_rows}"
        )
        loader.insert_log(
            "daily_feature_generator",
            target_date.strftime("%Y-%m-%d"),
            "SKIPPED_INSUFFICIENT_PRICE_DATA",
            0,
            (
                f"target_valid_rows={valid_rows}; previous_trading_day={previous_trading_day}; "
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
