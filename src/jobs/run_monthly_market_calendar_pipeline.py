from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

from src.collectors.market_calendar_collector import (
    fetch_market_calendar,
    load_market_calendar_overrides,
    resolve_market_calendar_name,
)
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, parse_date_string

logger = get_logger(__name__)

DEFAULT_EXCHANGES = ["XKRX", "XNYS"]
SUPPORTED_EXCHANGES = ["XKRX", "XNYS", "XNAS"]


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


def _resolve_exchanges(args) -> list[str]:
    if args.all_exchanges:
        return DEFAULT_EXCHANGES
    if args.exchange:
        exchange = str(args.exchange).strip().upper()
        if exchange == "ALL":
            return DEFAULT_EXCHANGES
        if exchange not in SUPPORTED_EXCHANGES:
            raise ValueError(f"Unsupported exchange code: {args.exchange}")
        return [exchange]
    return DEFAULT_EXCHANGES


def _ensure_table_exists(loader: SupabaseLoader) -> None:
    try:
        loader.client.table("market_trading_calendar").select("calendar_date").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            "market_trading_calendar table is missing. Run sql/add_market_trading_calendar.sql in Supabase SQL Editor first."
        ) from exc


def _summarize_exchange(rows: list[dict], exchange_code: str, calendar_name: str, dry_run: bool) -> dict:
    counter = Counter(row["reason"] for row in rows)
    open_days = sum(1 for row in rows if row["is_open"])
    closed_days = len(rows) - open_days
    override_dates = [row["calendar_date"] for row in rows if row.get("source") == "manual_override"]
    return {
        "exchange_code": exchange_code,
        "calendar_name": calendar_name,
        "total_days": len(rows),
        "open_days": open_days,
        "closed_days": closed_days,
        "weekend_days": counter.get("weekend", 0),
        "weekday_holidays": counter.get("holiday", 0),
        "first_open_day": next((row["calendar_date"] for row in rows if row["is_open"]), None),
        "last_open_day": next((row["calendar_date"] for row in reversed(rows) if row["is_open"]), None),
        "override_count": len(override_dates),
        "override_dates": override_dates,
        "dry_run": dry_run,
    }


def run_pipeline(
    start_date: date,
    end_date: date,
    exchanges: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    target_exchanges = exchanges or DEFAULT_EXCHANGES
    logger.info(
        f"Starting monthly market calendar pipeline start_date={start_date} "
        f"end_date={end_date} exchanges={target_exchanges} dry_run={dry_run}"
    )
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    if not dry_run:
        _ensure_table_exists(loader)

    all_rows: list[dict] = []
    exchange_summaries: list[dict] = []
    failures: list[str] = []
    overrides = load_market_calendar_overrides()

    for exchange_code in target_exchanges:
        try:
            calendar_name = resolve_market_calendar_name(exchange_code)
            rows = fetch_market_calendar(exchange_code, start_date, end_date)
            summary = _summarize_exchange(rows, exchange_code, calendar_name, dry_run)
            exchange_summaries.append(summary)
            all_rows.extend(rows)
            for key, value in summary.items():
                logger.info(f"{exchange_code}.{key}={value}")
            if exchange_code == "XKRX":
                logger.info(
                    f"{exchange_code}.override_applied_2026_05_06="
                    f"{'2026-05-06' in summary.get('override_dates', [])}"
                )
        except Exception as exc:
            failures.append(f"{exchange_code}: {exc}")
            logger.error(f"Failed to build market calendar for {exchange_code}: {exc}")

    result = {
        "target_start_date": start_date.isoformat(),
        "target_end_date": end_date.isoformat(),
        "exchanges": exchange_summaries,
        "total_rows": len(all_rows),
        "override_count": sum(summary.get("override_count", 0) for summary in exchange_summaries),
        "configured_override_keys": sorted(f"{exchange}:{calendar_date}" for exchange, calendar_date in overrides.keys()),
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info("Dry-run enabled; skipping market_trading_calendar upsert.")
        return result

    status = "SUCCESS"
    error_message = ""
    if failures and not all_rows:
        status = "FAIL"
        error_message = "; ".join(failures)
    else:
        if all_rows:
            ok = loader.upsert_records("market_trading_calendar", all_rows, raise_on_error=True)
            if not ok:
                status = "FAIL"
                error_message = "market_trading_calendar upsert failed"
        if failures and status != "FAIL":
            status = "WARN"
            error_message = "; ".join(failures)

    loader.insert_log(
        "monthly_market_calendar_pipeline",
        get_current_kst().date().isoformat(),
        status,
        len(all_rows),
        error_message,
    )
    if status == "FAIL":
        raise RuntimeError(error_message or "market_trading_calendar upsert failed")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    parser.add_argument("--exchange", type=str, choices=["ALL", "XKRX", "XNYS", "XNAS"])
    parser.add_argument("--all-exchanges", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    start_date, end_date = _resolve_range(args)
    exchanges = _resolve_exchanges(args)
    run_pipeline(start_date, end_date, exchanges=exchanges, dry_run=args.dry_run)
