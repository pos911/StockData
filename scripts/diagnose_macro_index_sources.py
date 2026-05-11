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

    print("\n--- 3. KIS Market APIs ---")
    collector = GlobalIndexCollector(config)
    
    kis_index_kospi = None
    kis_index_kosdaq = None
    kis_investor_kospi = None
    yahoo_kospi = None
    
    try:
        kospi_idx_raw = collector.fetch_kis_index_price("0001", target_date)
        print("KIS inquire-index-price KOSPI (0001) raw:", kospi_idx_raw)
        if kospi_idx_raw:
            kis_index_kospi = float(kospi_idx_raw.get("bstp_nmix_prpr", 0))
            
        kosdaq_idx_raw = collector.fetch_kis_index_price("1001", target_date)
        print("KIS inquire-index-price KOSDAQ (1001) raw:", kosdaq_idx_raw)
        if kosdaq_idx_raw:
            kis_index_kosdaq = float(kosdaq_idx_raw.get("bstp_nmix_prpr", 0))
            
        # For investor-daily-by-market, we need to manually call it or use the flow method which doesn't return raw easily anymore.
        # But we can call _fetch_market_daily_row directly
        import asyncio
        token = asyncio.run(collector._get_kis_access_token())
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": config["kis"]["app_key"],
            "appsecret": config["kis"]["app_secret"],
            "tr_id": "FHPTJ04040000",
            "custtype": "P",
        }
        kospi_inv_raw = collector._fetch_market_daily_row(headers, target_date.strftime("%Y%m%d"), "0001", "KSP")
        print("KIS inquire-investor-daily-by-market KOSPI raw:", kospi_inv_raw)
        if kospi_inv_raw:
            kis_investor_kospi = float(kospi_inv_raw.get("bstp_nmix_prpr", 0))
            
    except Exception as e:
        print("Failed to fetch from KIS:", e)
        
    print("\n--- 3.5 Final Evaluation ---")
    df = yf.Ticker("^KS11").history(period="1d", interval="1m")
    if not df.empty:
        yahoo_kospi = float(df.iloc[-1]["Close"])
        
    print(f"kis_index_price_kospi: {kis_index_kospi}")
    print(f"kis_index_price_kosdaq: {kis_index_kosdaq}")
    print(f"kis_investor_market_snapshot_kospi: {kis_investor_kospi}")
    print(f"yahoo_kospi: {yahoo_kospi}")
    
    final_val = None
    source = "UNKNOWN"
    quality = "MISSING"
    
    if kis_index_kospi is not None and 1000 <= kis_index_kospi <= 6500:
        final_val = kis_index_kospi
        source = "KIS"
        quality = "OK"
    elif yahoo_kospi is not None and 1000 <= yahoo_kospi <= 6500:
        final_val = yahoo_kospi
        source = "YAHOO"
        quality = "FALLBACK_YAHOO" if kis_index_kospi is None else "OK" # In our collector logic, it's INVALID if out of bounds. But wait, if KIS fails, it's FALLBACK.
    else:
        # If kis returned something but out of bounds
        if kis_index_kospi is not None:
            source = "KIS"
            quality = "INVALID"
        elif yahoo_kospi is not None:
            source = "YAHOO"
            quality = "INVALID"
            
    print(f"final_selected_kospi: {final_val}")
    print(f"final_selected_source: {source}")
    print(f"final_quality_flag: {quality}")

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
