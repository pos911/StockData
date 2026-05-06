from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.time_utils import get_kst_target_date


TARGET_TABLES = (
    "normalized_market_rankings_daily",
    "raw_market_rankings",
)
TARGET_MARKETS = ("KOSPI", "KOSDAQ", "ETF", "ETN")


def _closed_dates(loader: SupabaseLoader, exchange_code: str, start_date: str, end_date: str) -> list[str]:
    rows = loader.fetch_all("market_trading_calendar", "calendar_date", start_date, end_date)
    return sorted(
        {
            str(row.get("calendar_date"))[:10]
            for row in rows
            if row.get("exchange_code") == exchange_code and not row.get("is_open")
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="XKRX")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    today = get_kst_target_date()
    start_date = args.start_date or (today - timedelta(days=30)).isoformat()
    end_date = args.end_date or today.isoformat()

    closed_dates = _closed_dates(loader, args.exchange, start_date, end_date)
    print(f"exchange={args.exchange} closed_dates={closed_dates}")
    if not closed_dates:
        print("No closed dates found in range.")
        return 0

    summary = {}
    for table_name in TARGET_TABLES:
        rows = (
            loader.client.table(table_name)
            .select("base_date, market", count="exact")
            .in_("base_date", closed_dates)
            .in_("market", list(TARGET_MARKETS))
            .execute()
        )
        summary[table_name] = int(rows.count or 0)
    print(f"dry_run={not args.apply} summary={summary}")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete closed-market ranking rows.")
        return 0

    for table_name in TARGET_TABLES:
        (
            loader.client.table(table_name)
            .delete()
            .in_("base_date", closed_dates)
            .in_("market", list(TARGET_MARKETS))
            .execute()
        )
    print("Closed-market ranking rows deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
