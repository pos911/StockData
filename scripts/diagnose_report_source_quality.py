from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.report_source_quality import (
    WATCHLIST_SYMBOLS,
    analyze_feature_source_quality,
    analyze_report_views,
    analyze_watchlist_symbol,
    detect_macro_suspicious_values,
    fetch_latest_macro_row,
)
from src.utils.time_utils import parse_date_string


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False))


def main(target_date: date) -> None:
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    macro_row = fetch_latest_macro_row(loader, target_date) or {}
    suspicious_macro_values = detect_macro_suspicious_values(macro_row)
    us10y = macro_row.get("us10y")
    us3y = macro_row.get("us3y")
    spread_bp = ((float(us10y) - float(us3y)) * 100) if us10y is not None and us3y is not None else None

    print("[MACRO SOURCE QUALITY]")
    print(f"- target_date: {target_date.isoformat()}")
    print(f"- selected_macro_base_date: {str(macro_row.get('base_date') or '')[:10] or None}")
    for field in ("sp500", "nasdaq", "sox", "brent", "wti", "us10y", "us3y"):
        print(f"- {field}: {macro_row.get(field)}")
    print(f"- us10y_us3y_spread_bp: {spread_bp}")
    print(f"- suspicious_macro_values: {suspicious_macro_values}")
    print(f"- status: {'WARN_PRICE_SCALE_ANOMALY' if suspicious_macro_values else 'SUCCESS'}")

    for symbol in WATCHLIST_SYMBOLS:
        watch = analyze_watchlist_symbol(loader, symbol, target_date)
        print("\n[WATCHLIST PRICE QUALITY]")
        print(f"- symbol: {symbol}")
        print(f"- name: {watch.get('name')}")
        print(f"- latest_base_date: {watch.get('latest_base_date')}")
        print(f"- source: {watch.get('source')}")
        print(f"- close_price: {watch.get('close_price')}")
        print(f"- previous_price_source_summary: {_printable(watch.get('previous_price_source_summary'))}")
        print(f"- return_5d: {watch.get('return_5d')}")
        print(f"- return_20d: {watch.get('return_20d')}")
        print(f"- source_mixed: {watch.get('source_mixed')}")
        print(f"- price_scale_warning: {watch.get('price_scale_warning')}")
        print(f"- stale_days: {watch.get('stale_days')}")
        print(f"- status: {watch.get('status')}")

        feature = analyze_feature_source_quality(loader, symbol, target_date)
        print("\n[FEATURE SOURCE QUALITY]")
        print(f"- symbol: {symbol}")
        print(f"- return_5d: {feature.get('return_5d')}")
        print(f"- return_20d: {feature.get('return_20d')}")
        print(f"- trading_value_ratio_20d: {feature.get('trading_value_ratio_20d')}")
        print(f"- calculation_source_consistent: {feature.get('calculation_source_consistent')}")
        print(f"- source_mixed_warning: {_printable(feature.get('source_mixed_warning'))}")
        print(f"- status: {feature.get('status')}")

    etf_rows = (
        loader.client.table("report_sector_etf_signal_view")
        .select("*")
        .order("sector_group")
        .order("symbol")
        .execute()
        .data
        or []
    )
    for row in etf_rows:
        warnings = []
        if (row.get("stale_days") or 0) > 3 or str(row.get("data_status") or "").startswith("STALE"):
            warnings.append("STALE")
        print("\n[SECTOR ETF QUALITY]")
        print(f"- symbol: {row.get('symbol')}")
        print(f"- name: {row.get('name')}")
        print(f"- latest_price_date: {row.get('latest_price_date')}")
        print(f"- stale_days: {row.get('stale_days')}")
        print(f"- return_5d: {row.get('return_5d')}")
        print(f"- return_20d: {row.get('return_20d')}")
        print(f"- trading_value_ratio_20d: {row.get('trading_value_ratio_20d')}")
        print(f"- source: {row.get('data_status')}")
        print(f"- stale_or_scale_warning: {warnings}")
        print(f"- status: {'WARN_REPORT_SOURCE_STALE' if warnings else 'SUCCESS'}")

    view_quality = analyze_report_views(loader, target_date)
    print("\n[REPORT VIEW QUALITY]")
    print(f"- report_watchlist_snapshot_view rows: {view_quality['report_watchlist_snapshot_view_rows']}")
    print(f"- report_sector_etf_signal_view rows: {view_quality['report_sector_etf_signal_view_rows']}")
    print(f"- stale_watchlist_count: {view_quality['stale_watchlist_count']}")
    print(f"- stale_sector_etf_count: {view_quality['stale_sector_etf_count']}")
    print(f"- future_date_rows: {view_quality['future_date_rows']}")
    print(f"- status: {view_quality['status']}")


def _printable(value):
    return json.dumps(value, ensure_ascii=False) if value is not None else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    args = parser.parse_args()
    main(parse_date_string(args.date))
