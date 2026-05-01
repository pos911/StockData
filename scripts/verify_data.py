from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.time_utils import get_current_kst


DATE_COLUMNS = {
    "normalized_stock_prices_daily": "base_date",
    "normalized_stock_supply_daily": "base_date",
    "normalized_stock_short_selling": "base_date",
    "normalized_stock_snapshots_daily": "base_date",
    "normalized_market_rankings_daily": "base_date",
    "normalized_stock_fundamentals_ratios": "base_date",
    "normalized_stock_events_daily": "base_date",
    "normalized_macro_series": "base_date",
    "normalized_global_macro_daily": "base_date",
    "market_breadth_daily": "base_date",
    "feature_store_daily": "base_date",
    "raw_stock_prices_daily": "base_date",
    "raw_stock_supply_daily": "base_date",
    "raw_stock_short_selling": "base_date",
    "raw_market_rankings": "base_date",
    "raw_ecos_macro_daily": "date",
    "stocks_master": "updated_at",
    "macro_series_master": "updated_at",
}

LEGACY_TABLES = {"normalized_stock_fundamentals"}
CORE_MACRO_SERIES = ["KR_GOVT_10Y", "KR_GOVT_3Y", "USDKRW", "KR_CD_91D", "KR_CORP_AA_3Y"]


def _count(loader: SupabaseLoader, table: str) -> int:
    res = loader.client.table(table).select("*", count="exact").limit(1).execute()
    return int(res.count or 0)


def _latest(loader: SupabaseLoader, table: str, date_col: str) -> str | None:
    res = loader.client.table(table).select(date_col).order(date_col, desc=True).limit(1).execute()
    if not res.data:
        return None
    value = res.data[0].get(date_col)
    return str(value)[:10] if value else None


def _target_count(loader: SupabaseLoader, table: str, date_col: str, target_date: str) -> int:
    if date_col == "updated_at":
        return 0
    res = loader.client.table(table).select(date_col, count="exact").eq(date_col, target_date).limit(1).execute()
    return int(res.count or 0)


def _active_symbols(loader: SupabaseLoader) -> set[str]:
    rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    return {row["symbol"] for row in rows if row.get("is_active") and row.get("symbol")}


def _symbol_coverage(loader: SupabaseLoader, table: str, target_date: str, active: set[str]) -> tuple[float | None, int]:
    if table not in {
        "normalized_stock_prices_daily",
        "normalized_stock_supply_daily",
        "feature_store_daily",
        "normalized_stock_fundamentals_ratios",
    }:
        return None, 0
    rows = loader.fetch_all(table, "base_date", target_date, target_date)
    if table == "normalized_stock_prices_daily":
        rows = [
            row
            for row in rows
            if row.get("close_price") not in (None, "")
            and row.get("volume") not in (None, "")
            and row.get("trading_value") not in (None, "")
        ]
    covered = {row["symbol"] for row in rows if row.get("symbol") in active}
    missing = len(active - covered)
    coverage = len(covered) / max(len(active), 1)
    return coverage, missing


def _stale_days(latest_date: str | None, today: date) -> int | None:
    if not latest_date:
        return None
    try:
        return (today - date.fromisoformat(latest_date[:10])).days
    except ValueError:
        return None


def _status(table: str, row_count: int, target_count: int, stale: int | None, coverage: float | None) -> tuple[str, str]:
    if table in LEGACY_TABLES:
        return "LEGACY", "legacy table; not required for current report path"
    if row_count == 0:
        return "FAIL_EMPTY", "table has no rows"
    if stale is None:
        return "FAIL_SCHEMA", "date column could not be interpreted"
    if table == "normalized_stock_short_selling":
        if stale > 5:
            return "WARN_STALE", "short-selling can be sparse, but latest data is stale"
        return "SUCCESS", "short-selling table has recent rows"
    if coverage is not None:
        if table == "normalized_stock_prices_daily" and coverage < 0.7:
            return "WARN_COVERAGE", f"active symbol coverage={coverage:.1%}"
        if table == "normalized_stock_supply_daily" and coverage < 0.4:
            return "WARN_COVERAGE", f"active symbol coverage={coverage:.1%}"
    if stale > 5:
        return "WARN_STALE", f"latest row is {stale} days old"
    if target_count == 0 and stale > 0:
        return "WARN_NO_TARGET_ROWS", "no rows for target date"
    return "SUCCESS", "ok"


def _macro_series_status(loader: SupabaseLoader, today: date):
    print("\n=== Core Macro Series Freshness ===")
    print(f"{'series_id':<20} | {'latest_date':<12} | {'stale_days':<10} | {'status':<12}")
    print("-" * 65)
    for series_id in CORE_MACRO_SERIES:
        res = (
            loader.client.table("normalized_macro_series")
            .select("base_date")
            .eq("series_id", series_id)
            .order("base_date", desc=True)
            .limit(1)
            .execute()
        )
        latest = str(res.data[0]["base_date"])[:10] if res.data else None
        stale = _stale_days(latest, today)
        if stale is None:
            status = "FAIL_EMPTY"
        elif series_id in {"KR_GOVT_10Y", "USDKRW"} and stale > 5:
            status = "FAIL_STALE"
        elif series_id in {"KR_GOVT_10Y", "USDKRW"} and stale > 2:
            status = "WARN_STALE"
        elif stale > 5:
            status = "WARN_STALE"
        else:
            status = "SUCCESS"
        print(f"{series_id:<20} | {latest or 'N/A':<12} | {str(stale):<10} | {status:<12}")


def _print_price_quality(loader: SupabaseLoader):
    print("\n=== PRICE TABLE QUALITY ===")
    latest = _latest(loader, "normalized_stock_prices_daily", "base_date")
    if not latest:
        print("latest_price_date=N/A status=FAIL_NO_VALID_PRICE_ROWS note=no price rows")
        return
    rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest, latest)
    latest_total_rows = len(rows)
    null_close_rows = sum(1 for row in rows if row.get("close_price") in (None, ""))
    null_volume_rows = sum(1 for row in rows if row.get("volume") in (None, ""))
    null_trading_value_rows = sum(1 for row in rows if row.get("trading_value") in (None, ""))
    snapshot_only_rows = sum(
        1
        for row in rows
        if all(row.get(field) in (None, "") for field in ("open_price", "high_price", "low_price", "close_price", "volume", "trading_value"))
    )
    valid_price_rows = sum(
        1
        for row in rows
        if row.get("close_price") not in (None, "")
        and row.get("volume") not in (None, "")
        and row.get("trading_value") not in (None, "")
    )
    if snapshot_only_rows > 0:
        status = "FAIL_PRICE_QUALITY"
    elif valid_price_rows == 0:
        status = "FAIL_NO_VALID_PRICE_ROWS"
    elif latest_total_rows and valid_price_rows / latest_total_rows < 0.8:
        status = "WARN_PRICE_NULL_RATIO"
    else:
        status = "SUCCESS"
    note = (
        f"null_close={null_close_rows}, null_volume={null_volume_rows}, "
        f"null_trading_value={null_trading_value_rows}"
    )
    print(f"latest_price_date={latest}")
    print(f"latest_total_rows={latest_total_rows}")
    print(f"null_close_rows={null_close_rows}")
    print(f"null_volume_rows={null_volume_rows}")
    print(f"null_trading_value_rows={null_trading_value_rows}")
    print(f"snapshot_only_rows={snapshot_only_rows}")
    print(f"valid_price_rows={valid_price_rows}")
    print(f"status={status}")
    print(f"note={note}")


def verify_data():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    today = get_current_kst().date()
    target_date = today.isoformat()
    active = _active_symbols(loader)

    print("\n=== DATA QUALITY SUMMARY ===\n")
    print(
        f"{'table_name':<38} | {'row_count':<9} | {'latest_date':<12} | "
        f"{'target_count':<12} | {'coverage':<10} | {'missing':<8} | {'stale':<5} | {'status':<18} | note"
    )
    print("-" * 150)

    for table, date_col in DATE_COLUMNS.items():
        try:
            row_count = _count(loader, table)
            latest = _latest(loader, table, date_col) if row_count else None
            target_count = _target_count(loader, table, date_col, target_date) if row_count else 0
            coverage, missing = _symbol_coverage(loader, table, latest or target_date, active)
            stale = _stale_days(latest, today)
            status, note = _status(table, row_count, target_count, stale, coverage)
            coverage_text = f"{coverage:.1%}" if coverage is not None else "-"
            print(
                f"{table:<38} | {row_count:<9} | {latest or 'N/A':<12} | "
                f"{target_count:<12} | {coverage_text:<10} | {missing:<8} | {str(stale):<5} | {status:<18} | {note}"
            )
        except Exception as exc:
            print(f"{table:<38} | ERROR     | N/A          | 0            | -          | 0        | N/A   | FAIL_SCHEMA        | {exc}")

    for table in sorted(LEGACY_TABLES):
        try:
            row_count = _count(loader, table)
        except Exception:
            row_count = 0
        print(f"{table:<38} | {row_count:<9} | N/A          | 0            | -          | 0        | N/A   | LEGACY             | legacy table; not required")

    _print_price_quality(loader)
    _macro_series_status(loader, today)


if __name__ == "__main__":
    verify_data()
