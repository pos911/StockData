import argparse
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from src.utils.logger import get_logger
from src.utils.time_utils import generate_available_at_for_eod, get_current_utc, get_kst_target_date
from src.collectors.derivatives_collector import DerivativesCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.trading_calendar import get_previous_trading_day, should_skip_market_job

logger = get_logger(__name__)

def run_pipeline(target_date: date):
    logger.info(
        f"Starting Daily Derivatives Pipeline for {target_date} "
        f"(runner_date_utc={get_current_utc().date()}, target_date_kst={target_date})..."
    )
    
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    skip, reason = should_skip_market_job(loader, target_date, "XKRX", "daily_derivatives_pipeline")
    logger.info(f"xkrx_is_open={not skip} reason={reason}")
    if skip:
        previous_trading_day = get_previous_trading_day(loader, target_date, "XKRX")
        message = (
            f"XKRX market closed on {target_date:%Y-%m-%d}; skipped Korean market data ingestion. "
            f"previous_trading_day={previous_trading_day} reason={reason}"
        )
        logger.warning(message)
        loader.insert_log(
            "daily_derivatives_pipeline",
            target_date.strftime("%Y-%m-%d"),
            "SKIPPED_MARKET_CLOSED",
            0,
            message,
        )
        return
    collector = DerivativesCollector()
    
    available_at = generate_available_at_for_eod(target_date)
    
    total_processed = 0
    data = collector.fetch_daily_derivatives(target_date)
    
    if data:
        record = {
            "base_date": data["base_date"],
            "kospi200_futures": data.get("kospi200_futures"),
            "futures_basis": data.get("futures_basis"),
            "open_interest": data.get("open_interest"),
            "night_futures_return": data.get("night_futures_return"),
            "expiration_flag": data.get("expiration_flag"),
            "available_at": available_at.isoformat()
        }
        loader.upsert_records("normalized_derivatives_daily", [record])
        total_processed += 1

    status = "SUCCESS" if total_processed > 0 else "WARN"
    if status != "SUCCESS":
        logger.warning(f"Derivatives Pipeline finished with no processed records for {target_date}.")
    loader.insert_log("daily_derivatives_pipeline", target_date.strftime("%Y-%m-%d"), status, total_processed)
    logger.info(f"Derivatives Pipeline Finished. status={status}, processed={total_processed}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_pipeline(target_dt)
