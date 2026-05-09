from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.report_source_quality import analyze_universe_alignment
from src.utils.time_utils import parse_date_string


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    args = parser.parse_args()
    target_date = parse_date_string(args.date)

    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    result = analyze_universe_alignment(loader, target_date)

    print("[UNIVERSE ALIGNMENT]")
    print(f"- static_enabled_count: {result['static_enabled_count']}")
    print(f"- kis_detail_universe_count: {result['kis_detail_universe_count']}")
    print(f"- kis_detail_price_symbols_count: {result['kis_detail_price_symbols_count']}")
    print(f"- feature_symbol_count: {result['feature_symbol_count']}")
    print(f"- report_watchlist_count: {result['report_watchlist_count']}")
    print(f"- kis_volume_symbol_count: {result['kis_volume_symbol_count']}")
    print(f"- missing_in_kis_detail: {json.dumps(result['missing_in_kis_detail'], ensure_ascii=False)}")
    print(f"- missing_in_feature: {json.dumps(result['missing_in_feature'], ensure_ascii=False)}")
    print(f"- missing_in_report_view: {json.dumps(result['missing_in_report_view'], ensure_ascii=False)}")
    print(f"- stale_in_report_view: {json.dumps(result['stale_in_report_view'], ensure_ascii=False)}")
    print(f"- status: {result['status']}")


if __name__ == "__main__":
    main()
