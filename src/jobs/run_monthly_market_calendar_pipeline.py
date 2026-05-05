from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

from src.collectors.market_calendar_collector import fetch_krx_calendar
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, parse_date_string

logger = get_logger(__name__)


def _parse_cli_date(value: str) -> date:
    text = str(value).strip()
    if "-" in text:
        return date.fromisoformat(text)
    return parse_date_string(text)


def _resolve_range(args) -> tuple[date, date]:
    today = get_current_kst().date()
    if args.start_date and args.end_date:
        return _parse_cli_date(args.start_date), _parse_cli_date(args.end_date)
    if args.year:
        return date(args.year, 1, 1), date(args.year, 12, 31)
    return date(today.year, 1, 1), date(today.year + 1, 12, 31)


def _ensure_table_exists(loader: SupabaseLoader) -> None:
    try:
        loader.client.table("market_trading_calendar").select("calendar_date").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            "market_trading_calendar table is missing. Run sql/add_market_trading_calendar.sql in Supabase SQL Editor first."
        ) from exc


def run_pipeline(
    start_date: date,
    end_date: date,
    dry_run: bool = False,
) -> dict:
    logger.info(f"Starting monthly market calendar pipeline start_date={start_date} end_date={end_date} dry_run={dry_run}")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    if not dry_run:
        _ensure_table_exists(loader)

    rows = fetch_krx_calendar(start_date, end_date)
    counter = Counter(row["reason"] for row in rows)
    open_days = sum(1 for row in rows if row["is_open"])
    closed_days = len(rows) - open_days
    summary = {
        "target_start_date": start_date.isoformat(),
        "target_end_date": end_date.isoformat(),
        "exchange_code": rows[0]["exchange_code"] if rows else "XKRX",
        "total_days": len(rows),
        "open_days": open_days,
        "closed_days": closed_days,
        "weekend_days": counter.get("weekend", 0),
        "weekday_holidays": counter.get("holiday", 0),
        "first_open_day": next((row["calendar_date"] for row in rows if row["is_open"]), None),
        "last_open_day": next((row["calendar_date"] for row in reversed(rows) if row["is_open"]), None),
        "dry_run": dry_run,
    }

    for key, value in summary.items():
        logger.info(f"{key}={value}")

    if dry_run:
        logger.info("Dry-run enabled; skipping market_trading_calendar upsert.")
        return summary

    ok = loader.upsert_records("market_trading_calendar", rows, raise_on_error=True)
    status = "SUCCESS" if ok else "FAIL"
    loader.insert_log(
        "monthly_market_calendar_pipeline",
        get_current_kst().date().isoformat(),
        status,
        len(rows),
        "" if ok else "market_trading_calendar upsert failed",
    )
    if not ok:
        raise RuntimeError("market_trading_calendar upsert failed")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    start_date, end_date = _resolve_range(args)
    run_pipeline(start_date, end_date, dry_run=args.dry_run)
