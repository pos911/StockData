import argparse
import sys
import json
from datetime import date
from pathlib import Path
import yfinance as yf

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.collectors.global_index_collector import GlobalIndexCollector
from src.utils.time_utils import get_current_utc, get_kst_target_date, parse_date_string

def diagnose_macro_sources(target_date: date):
    print(f"\n=== MACRO SOURCES DIAGNOSTIC for {target_date} ===\n")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    print("--- 1. YFinance Daily ---")
    for ticker in ["^KS11", "^KQ11", "KRW=X"]:
        df = yf.Ticker(ticker).history(period="7d", interval="1d")
        if not df.empty:
            last = df.iloc[-1]
            print(f"{ticker}: {last['Close']} (date: {df.index[-1]})")
        else:
            print(f"{ticker}: NO DATA")

    print("\n--- 2. YFinance Intraday (1d, 1m/5m) ---")
    for ticker in ["^KS11", "^KQ11", "KRW=X"]:
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if df.empty:
            df = yf.Ticker(ticker).history(period="1d", interval="5m")
        if not df.empty:
            last = df.iloc[-1]
            print(f"{ticker}: {last['Close']} (observed_at: {df.index[-1]})")
        else:
            print(f"{ticker}: NO DATA")

    print("\n--- 3. KIS Market Snapshot ---")
    collector = GlobalIndexCollector(config)
    try:
        kis_data = collector._fetch_korean_market_snapshot(target_date)
        print("KIS kospi:", kis_data.get("kospi"))
        print("KIS kosdaq:", kis_data.get("kosdaq"))
        # We can't easily print raw KIS rows without intercepting requests, but we know what the collector outputs
    except Exception as e:
        print("Failed to fetch from KIS:", e)

    print("\n--- 4. Supabase: normalized_macro_series ---")
    for series in ["USDKRW", "DEXKOUS", "KRW=X"]:
        res = loader.client.table("normalized_macro_series").select("*").eq("series_id", series).order("base_date", desc=True).limit(2).execute()
        for r in (res.data or []):
            print(f"{r['series_id']} | base_date={r['base_date']} | value={r['value']} | updated={r['updated_at']}")

    print("\n--- 5. Supabase: normalized_global_macro_daily ---")
    try:
        res = loader.client.table("normalized_global_macro_daily").select("base_date, kospi, kospi_source, kospi_quality_flag, kosdaq, kosdaq_source, kosdaq_quality_flag, usdkrw, usdkrw_source, usdkrw_quality_flag").order("base_date", desc=True).limit(3).execute()
        for r in (res.data or []):
            print(f"[{r['base_date']}] KOSPI={r['kospi']} ({r.get('kospi_source')}, {r.get('kospi_quality_flag')}) | KOSDAQ={r['kosdaq']} ({r.get('kosdaq_source')}, {r.get('kosdaq_quality_flag')}) | USDKRW={r['usdkrw']} ({r.get('usdkrw_source')}, {r.get('usdkrw_quality_flag')})")
    except Exception as e:
        print(f"Failed to fetch global_macro_daily (maybe schema missing?): {e}")

    print("\n--- 6. Supabase: normalized_macro_intraday ---")
    try:
        res = loader.client.table("normalized_macro_intraday").select("*").order("observed_at", desc=True).limit(10).execute()
        for r in (res.data or []):
            print(f"[{r['observed_at']}] {r['series_id']}={r['value']} ({r.get('source')}, {r.get('quality_flag')})")
    except Exception as e:
        print(f"Failed to fetch macro_intraday (maybe schema missing?): {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYY-MM-DD")
    args = parser.parse_args()
    
    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
        target_dt = parse_date_string(args.date)
        
    diagnose_macro_sources(target_dt)
