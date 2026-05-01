from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Sequence

sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.collectors.ecos_client import EcosClient
from src.collectors.ecos_fx import EcosFXCollector
from src.collectors.ecos_rates import EcosRatesCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect ECOS macro series.")
    parser.add_argument("--series", help="Single series_id to collect.")
    parser.add_argument("--start", help="Start date in YYYYMMDD.")
    parser.add_argument("--end", help="End date in YYYYMMDD.")
    parser.add_argument("--days", type=int, help="Collect only the last N calendar days.")
    parser.add_argument("--lookback-days", type=int, default=14, help="Fallback lookback window when incremental ECOS has no rows.")
    parser.add_argument("--all", action="store_true", help="Collect all configured ECOS series.")
    return parser.parse_args()


def _resolve_range(args: argparse.Namespace) -> tuple[str | None, str | None]:
    today = get_current_kst().date()
    end_date = args.end or today.strftime("%Y%m%d")
    start_date = args.start
    if args.days:
        start_date = (today - timedelta(days=args.days)).strftime("%Y%m%d")
    return start_date, end_date


def _collect_targets(args: argparse.Namespace) -> Sequence[str] | None:
    if args.series:
        return [args.series]
    if args.all:
        return None
    return None


def run_collection(args: argparse.Namespace) -> Dict[str, List[str] | int]:
    config = load_config()
    api_key = config.get("ecos", {}).get("api_key")
    if not api_key:
        raise ValueError("ECOS API key is missing from config/api_keys.json or environment.")

    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    client = EcosClient(api_key=api_key)

    series_ids = _collect_targets(args)
    start_date, end_date = _resolve_range(args)

    rates_collector = EcosRatesCollector(client=client, loader=loader)
    fx_collector = EcosFXCollector(client=client, loader=loader)

    logger.info(
        "Starting ECOS macro collection. "
        f"series={series_ids if series_ids else 'ALL'} start={start_date or 'incremental'} end={end_date}"
    )

    rates_result = rates_collector.collect(
        series_ids=series_ids,
        start_date=start_date,
        end_date=end_date,
        lookback_days=args.lookback_days,
    )
    fx_result = fx_collector.collect(
        series_ids=series_ids,
        start_date=start_date,
        end_date=end_date,
        lookback_days=args.lookback_days,
    )

    raw_records = rates_result["raw_records"] + fx_result["raw_records"]
    normalized_records = rates_result["normalized_records"] + fx_result["normalized_records"]
    master_records = rates_result["master_records"] + fx_result["master_records"]
    warnings = rates_result["warnings"] + fx_result["warnings"]
    failures = rates_result["failures"] + fx_result["failures"]
    legacy_raw_records = [
        {
            "source": "ECOS",
            "series_id": row["series_id"],
            "base_date": row["date"],
            "raw_data": row["raw"],
            "collected_at": row["collected_at"],
            "available_at": row["collected_at"],
        }
        for row in raw_records
    ]

    try:
        if master_records:
            loader.upsert_records("macro_series_master", master_records, raise_on_error=True)
        if normalized_records:
            loader.upsert_records("normalized_macro_series", normalized_records, raise_on_error=True)
        if legacy_raw_records:
            loader.upsert_records("raw_macro_series", legacy_raw_records, raise_on_error=True)
    except Exception as exc:
        failures.append(str(exc))

    if raw_records:
        raw_ecos_ok = loader.upsert_records("raw_ecos_macro_daily", raw_records)
        if not raw_ecos_ok:
            warnings.append("raw_ecos_macro_daily upsert skipped; normalized data still loaded via fallback path.")

    if failures:
        status = "WARN"
    elif normalized_records:
        status = "SUCCESS"
    elif warnings:
        status = "WARN_NO_NEW_DATA"
    else:
        status = "SUCCESS_WITH_NO_NEW_DATA"
    records_processed = len(normalized_records)
    error_message = "; ".join(failures[:10])
    loader.insert_log("daily_ecos_macro_pipeline", end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:8], status, records_processed, error_message)

    if warnings:
        logger.warning("ECOS collection warnings: " + " | ".join(warnings[:10]))
    if failures:
        logger.warning("ECOS collection failures: " + " | ".join(failures[:10]))

    logger.info(
        f"ECOS macro collection finished. status={status}, "
        f"master={len(master_records)}, raw={len(raw_records)}, normalized={records_processed}"
    )
    return {
        "master_records": len(master_records),
        "raw_records": len(raw_records),
        "normalized_records": records_processed,
        "warnings": warnings,
        "failures": failures,
    }


if __name__ == "__main__":
    run_collection(_parse_args())
