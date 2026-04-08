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
            
            # [최적화] 최근 7일치 데이터만 증분 수집 (과거 전체가 아닌 새로운 값 위주)
            from datetime import timedelta
            obs_start = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
            
            logger.info(f"Fetching FRED series: {series_id} since {obs_start}")
            raw_data = fred.fetch_series(series_id=series_id, observation_start=obs_start, sort_order="desc", limit=100)
            obs = raw_data.get("observations", []) if raw_data else []
            
            if obs:
                # Raw 적재 (최근 값 1건만 한다고 가정 시 로직 조정 필요. 본 예제는 단순화)
                # 실제론 obs 전체 혹은 일부를 raw 테이블에 넣음.
                raw_record = {
                    "source": "FRED",
                    "series_id": series_id,
                    "base_date": target_date.strftime("%Y-%m-%d"), # 가장 최신 기준
                    "raw_data": json.dumps(raw_data),
                    "collected_at": timestamp_now.isoformat(),
                    "available_at": available_at.isoformat()
                }
                loader.upsert_records("raw_macro_series", [raw_record])
                
                # 정규화
                norm_records = MacroNormalizer.normalize_fred(series_id, obs, available_at)
                loader.upsert_records("normalized_macro_series", norm_records)
                total_processed += len(norm_records)

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
