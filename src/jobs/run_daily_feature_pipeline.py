import json
import pandas as pd
from datetime import date
import argparse
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst
from src.loaders.supabase_loader import SupabaseLoader

logger = get_logger(__name__)

from src.utils.config_loader import load_config

def run_feature_pipeline(target_date: date):
    logger.info(f"Starting Feature Pipeline for {target_date}...")
    
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    # 1. 시세 데이터 조회 (지표 계산을 위해 최근 60일치 확보)
    from datetime import timedelta
    start_date = (target_date - timedelta(days=60)).strftime("%Y-%m-%d")
    
    query = loader.client.table("normalized_stock_prices_daily") \
        .select("symbol, base_date, close_price, available_at") \
        .gte("base_date", start_date) \
        .order("base_date", desc=False)
    
    res = query.execute()
    if not res.data:
        logger.warning("No price data for feature calculation.")
        return

    df = pd.DataFrame(res.data)
    df['base_date'] = pd.to_datetime(df['base_date'])
    
    total_processed = 0
    feature_records = []

    # 2. 종목별 지표 계산
    for symbol, group in df.groupby('symbol'):
        group = group.sort_values('base_date')
        
        # 5일 이동평균
        group['ma5'] = group['close_price'].rolling(window=5).mean()
        # 20일 이동평균
        group['ma20'] = group['close_price'].rolling(window=20).mean()
        
        # 최신 날짜의 데이터만 추출
        latest_row = group[group['base_date'].dt.date == target_date]
        if latest_row.empty:
            continue
            
        row = latest_row.iloc[0]
        if pd.notnull(row['ma5']):
            feature_records.append({
                "symbol": symbol,
                "base_date": target_date.strftime("%Y-%m-%d"),
                "feature_name": "MA5",
                "feature_value": float(row['ma5']),
                "available_at": row['available_at']
            })
        if pd.notnull(row['ma20']):
            feature_records.append({
                "symbol": symbol,
                "base_date": target_date.strftime("%Y-%m-%d"),
                "feature_name": "MA20",
                "feature_value": float(row['ma20']),
                "available_at": row['available_at']
            })
            
    # 3. Feature Store 적재
    if feature_records:
        loader.upsert_records("feature_store_daily", feature_records)
        total_processed = len(feature_records)

    loader.insert_log("daily_feature_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", total_processed)
    logger.info("Feature Pipeline Finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_feature_pipeline(target_dt)
