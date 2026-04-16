import os
import json
from datetime import date
import argparse

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.collectors.fred_collector import FREDCollector
from src.normalizers.macro_normalizer import MacroNormalizer
from src.loaders.supabase_loader import SupabaseLoader

logger = get_logger(__name__)

from src.utils.config_loader import load_config

def load_macro_series():
    with open("config/macro_series.json", "r", encoding="utf-8") as f:
        return json.load(f)

def run_pipeline(target_date: date):
    logger.info(f"Starting Daily Macro Pipeline up to {target_date}...")
    
    config = load_config()
    series_list = load_macro_series()
    
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    fred = FREDCollector(api_key=config.get("fred", {}).get("api_key", ""))
    

    
    import yfinance as yf
    
    # 0. 마스터 정보 동기화
    logger.info("Syncing macro master metadata...")
    master_records = []
    for series in series_list:
        master_records.append({
            "series_id": series.get("series_id") or series.get("endpoint_key"),
            "source": series.get("source"),
            "name": series.get("name"),
            "frequency": series.get("frequency")
        })
    loader.upsert_records("macro_series_master", master_records)
    
    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    
    total_processed = 0

    for series in series_list:
        if not series.get("enabled", False):
            continue
            
        source = series.get("source")
        # 최근 1주일치만 가져와서 갱신한다고 가정 (부분 업데이트)
        # obs_start = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
        
        if source == "FRED":
            series_id = series.get("series_id")
            from datetime import timedelta
            obs_start = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
            
            logger.info(f"Fetching FRED series: {series_id} since {obs_start}")
            raw_data = fred.fetch_series(series_id=series_id, observation_start=obs_start, sort_order="desc", limit=100)
            obs = raw_data.get("observations", []) if raw_data else []
            
            if obs:
                # Raw 적재
                raw_record = {
                    "source": "FRED",
                    "series_id": series_id,
                    "base_date": target_date.strftime("%Y-%m-%d"),
                    "raw_data": json.dumps(raw_data),
                    "collected_at": timestamp_now.isoformat(),
                    "available_at": available_at.isoformat()
                }
                loader.upsert_records("raw_macro_series", [raw_record])
                
                # 정규화
                norm_records = MacroNormalizer.normalize_fred(series_id, obs, available_at)
                for r in norm_records:
                    keys_to_remove = [k for k in list(r.keys()) if k not in ['series_id', 'base_date', 'value']]
                    for k in keys_to_remove:
                        r.pop(k, None)
                    if 'value' in r and r['value'] is not None:
                        r['value'] = float(r['value'])
                loader.upsert_records("normalized_macro_series", norm_records)
                total_processed += len(norm_records)

        elif source == "YAHOO":
            series_id = series.get("series_id")
            logger.info(f"Fetching YAHOO series: {series_id}")
            try:
                from datetime import timedelta
                df = yf.download(series_id, start=target_date - timedelta(days=5), end=target_date + timedelta(days=1), progress=False)
                if not df.empty:
                    norm_record = {
                        "series_id": series_id,
                        "base_date": df.index[-1].strftime("%Y-%m-%d"),
                        "value": float(df["Close"].iloc[-1])
                    }
                    loader.upsert_records("normalized_macro_series", [norm_record])
                    total_processed += 1
            except Exception as e:
                logger.error(f"Failed to fetch YAHOO series {series_id}: {e}")

    # KRX Market Breadth 수집 (코스피)
    from src.collectors.krx_collector import KRXCollector
    krx = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    breadth_data = krx.fetch_market_breadth(target_date)
    
    # 파생 매크로 (Global Index) 수집 및 적재 
    from src.collectors.global_index_collector import GlobalIndexCollector
    global_index = GlobalIndexCollector()
    global_data = global_index.fetch_daily_indices(target_date)
    
    # FRED: High Yield Spread (BAMLH0A0HYM2) - 별도 추출 (Global Macro Daily용)
    hy_spread = None
    raw_hy = fred.fetch_series(series_id="BAMLH0A0HYM2", observation_start=target_date.strftime("%Y-%m-%d"), limit=1)
    if raw_hy and raw_hy.get("observations"):
        try:
            val = raw_hy["observations"][0]["value"]
            if val != ".": # FRED missing data marker
                hy_spread = float(val)
        except (ValueError, TypeError):
            pass

    if global_data:
        global_record = {
            "base_date": global_data.get("base_date"),
            "usdkrw": global_data.get("usdkrw"),
            "dxy": global_data.get("dxy"),
            "us10y": global_data.get("us10y"),
            "kr10y": global_data.get("kr10y"),
            "wti": global_data.get("wti"),
            "brent": global_data.get("brent"),
            "nasdaq": global_data.get("nasdaq"),
            "sp500": global_data.get("sp500"),
            "sox": global_data.get("sox"),
            "vix": global_data.get("vix"),
            # 신규 추가 지표
            "gold": global_data.get("gold"),
            "copper": global_data.get("copper"),
            "bdry": global_data.get("bdry"),
            "hy_spread": hy_spread,
            "available_at": available_at.isoformat()
        }
        
        # Market Breadth는 별도 테이블에 저장 (글로벌 매크로와 분리)
        if breadth_data:
            breadth_record = {
                "base_date": target_date.strftime("%Y-%m-%d"),
                **breadth_data,
                "available_at": available_at.isoformat()
            }
            loader.upsert_records("market_breadth_daily", [breadth_record])
            total_processed += 1
            
        loader.upsert_records("normalized_global_macro_daily", [global_record])
        total_processed += 1

    loader.insert_log("daily_macro_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", total_processed)
    logger.info("Macro Pipeline Finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_pipeline(target_dt)
