import argparse
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_utc, get_kst_target_date, parse_date_string
from src.collectors.intraday_macro_collector import IntradayMacroCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.trading_calendar import should_skip_market_job

logger = get_logger(__name__)

def run_pipeline(target_date: date, dry_run: bool = False):
    logger.info(f"Starting Intraday Macro Pipeline for {target_date}...")
    
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    collector = IntradayMacroCollector(config)
    results = collector.fetch_snapshots(target_date)
    
    if not results:
        logger.warning("No intraday macro data collected.")
        if not dry_run:
            loader.insert_log("intraday_macro_pipeline", target_date.strftime("%Y-%m-%d"), "FAIL", 0, "No data collected")
        return
        
    kr_skip, kr_reason = should_skip_market_job(loader, target_date, "XKRX", "intraday_macro_pipeline")
        
    row_count = len(results)
    invalid_count = sum(1 for r in results if r["quality_flag"] == "INVALID")
    stale_count = sum(1 for r in results if r["quality_flag"] == "FALLBACK_DAILY")
    
    series_flags = {r["series_id"]: r["quality_flag"] for r in results}
    usdkrw_flag = series_flags.get("USDKRW")
    kospi_flag = series_flags.get("KOSPI")
    kosdaq_flag = series_flags.get("KOSDAQ")
    
    if not dry_run:
        upserted = loader.upsert_records("normalized_macro_intraday", results)
        if not upserted:
            logger.error("Failed to upsert normalized_macro_intraday records.")
            
    # Determine status
    status = "SUCCESS"
    error_msg = f"row_count={row_count}, invalid={invalid_count}, stale={stale_count}"
    
    if not usdkrw_flag or usdkrw_flag == "INVALID":
        status = "FAIL"
        error_msg += " | USDKRW missing or invalid"
    elif kr_skip:
        status = "WARN"
        error_msg += f" | SKIPPED_MARKET_CLOSED ({kr_reason})"
    elif not kospi_flag or kospi_flag == "INVALID" or not kosdaq_flag or kosdaq_flag == "INVALID":
        status = "WARN"
        error_msg += " | KOSPI or KOSDAQ missing/invalid"
    elif invalid_count > 0 or stale_count > 0:
        status = "WARN"
    
    logger.info(f"Intraday Macro Pipeline finished with status: {status}. {error_msg}")
    
    if not dry_run:
        loader.insert_log(
            "intraday_macro_pipeline",
            target_date.strftime("%Y-%m-%d"),
            status,
            row_count,
            error_msg
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
        target_dt = parse_date_string(args.date)

    run_pipeline(target_dt, dry_run=args.dry_run)
