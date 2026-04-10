import os
import json
import argparse
import pandas as pd
import numpy as np
from datetime import date, timedelta, datetime
from typing import List, Dict, Any

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config

logger = get_logger(__name__)

class FeatureGenerator:
    """
    Normalized 데이터를 바탕으로 실제 Trading Algorithmic Feature들을 생성합니다.
    pandas를 사용하여 이동평균, 변동성, 수급 Z-Score 등을 계산합니다.
    """
    def __init__(self, loader: SupabaseLoader):
        self.loader = loader

    def generate_features_for_date(self, target_date: date):
        # 1. 대상 종목 리스트 (Universe) 가져오기
        universe = self._load_universe()
        enabled_symbols = [s["symbol"] for s in universe if s.get("enabled", False)]
        
        if not enabled_symbols:
            logger.warning("No enabled symbols found in universe.")
            return

        # 2. 데이터 조회 기간 설정 (최근 60일치 확보)
        start_date = target_date - timedelta(days=90) # 주말/공휴일 고려하여 넉넉히 90일
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = target_date.strftime("%Y-%m-%d")
        
        logger.info(f"Fetching data from {start_date_str} to {end_date_str} for {len(enabled_symbols)} symbols...")

        # 3. 데이터 로드 (Price & Supply)
        # Note: 대량 조회를 위해 in_. 연산자 활용 추천되나, 여기선 심플하게 전체 조회 후 필터링하거나 루프 처리
        # 성능을 위해 전체를 한 번에 가져오는 방식으로 구현
        
        prices_df = self._fetch_table_data("normalized_stock_prices_daily", start_date_str, end_date_str)
        supply_df = self._fetch_table_data("normalized_stock_supply_daily", start_date_str, end_date_str)

        if prices_df.empty or supply_df.empty:
            logger.error("Required data (Prices or Supply) is missing in Supabase.")
            return

        # 4. 데이터 병합 (Merge)
        # 주가 데이터가 기준이 되도록 how='left' 사용
        df = pd.merge(prices_df, supply_df, on=["symbol", "base_date"], how="left")
        
        # 수급 데이터 누락 시 0으로 채움
        df["foreign_net_buy"] = df["foreign_net_buy"].fillna(0)
        
        if df.empty:
            logger.warning("Merged dataframe is empty. Ensure dates match between Price and Supply tables.")
            return

        # 날짜 정렬 (계산을 위해 필수)
        df = df.sort_values(["symbol", "base_date"]).reset_index(drop=True)

        # 5. 피처 계산 (Feature Engineering)
        logger.info("Calculating technical and supply features...")
        
        feature_records = []
        available_at_str = generate_available_at_for_eod(target_date).isoformat()
        
        for symbol, group in df.groupby("symbol"):
            group = group.sort_values("base_date")
            
            # 가격 지표
            close = group["close_price"]
            group["return_5d"] = close.pct_change(5)
            group["moving_avg_5"] = close.rolling(5).mean()
            group["moving_avg_20"] = close.rolling(20).mean()
            # 20일 변동성 (수익률의 표준편차)
            group["volatility_20d"] = close.pct_change().rolling(20).std()
            
            # 수급 지표 (외국인 순매수 Z-Score)
            foreign_buy = group["foreign_net_buy"]
            f_mean = foreign_buy.rolling(20).mean()
            f_std = foreign_buy.rolling(20).std()
            group["foreign_flow_zscore"] = (foreign_buy - f_mean) / f_std.replace(0, np.nan)
            
            # 마지막 날짜(target_date) 행 추출
            last_row = group[group["base_date"] == end_date_str]
            if last_row.empty:
                continue
                
            row = last_row.iloc[0]
            
            # 결과물 리스트업
            targets = {
                "return_5d": row.get("return_5d"),
                "moving_avg_5": row.get("moving_avg_5"),
                "moving_avg_20": row.get("moving_avg_20"),
                "volatility_20d": row.get("volatility_20d"),
                "foreign_flow_zscore": row.get("foreign_flow_zscore")
            }
            
            for f_name, f_val in targets.items():
                if pd.notnull(f_val):
                    feature_records.append({
                        "symbol": symbol,
                        "base_date": end_date_str,
                        "feature_name": f_name,
                        "feature_value": float(f_val),
                        "available_at": available_at_str
                    })

        # 6. Feature Store 저장
        if feature_records:
            self.loader.upsert_records("feature_store_daily", feature_records)
            logger.info(f"Successfully upserted {len(feature_records)} real features for {target_date}.")
        else:
            logger.warning("No features were generated (might be due to insufficient history).")

    def _fetch_table_data(self, table_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Supabase에서 특정 기간의 데이터를 DataFrame으로 로드"""
        try:
            # 쿼리: base_date >= start AND base_date <= end
            res = self.loader.client.table(table_name).select("*")\
                .gte("base_date", start_date)\
                .lte("base_date", end_date)\
                .execute()
            
            return pd.DataFrame(res.data)
        except Exception as e:
            logger.error(f"Error fetching {table_name}: {e}")
            return pd.DataFrame()

    def _load_universe(self):
        with open("config/stock_universe.json", "r", encoding="utf-8") as f:
            return json.load(f)

def run_job(target_date: date):
    logger.info(f"Starting Real Feature Engineering Job for {target_date}...")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    generator = FeatureGenerator(loader)
    generator.generate_features_for_date(target_date)
    
    loader.insert_log("daily_feature_generator", target_date.strftime("%Y-%m-%d"), "SUCCESS", 0)
    logger.info("Feature Engineering Finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_job(target_dt)
