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
        enabled_symbols = [s["symbol"] for s in universe]
        
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

        if prices_df.empty:
            logger.error("Required Price data is missing in Supabase.")
            return

        if supply_df.empty:
            logger.warning("Supply data is empty. Proceeding with price data only.")
            df = prices_df.copy()
            df["foreign_net_buy"] = 0
        else:
            # 4. 데이터 병합 (Merge)
            df = pd.merge(prices_df, supply_df, on=["symbol", "base_date"], how="left")
            df["foreign_net_buy"] = df["foreign_net_buy"].fillna(0)

        # 날짜 통일 및 정렬 (요구사항 1: strftime 강제 변환)
        df["base_date"] = pd.to_datetime(df["base_date"]).dt.strftime('%Y-%m-%d')
        target_pd_date = end_date_str

        # 로드된 전체 데이터의 날짜 범위를 로그로 출력
        min_date = df["base_date"].min()
        max_date = df["base_date"].max()
        logger.info(f"Data Range: {min_date} to {max_date}")

        # 요구사항 2: 전체 결과 볼륨 출력
        logger.info(f"DB Fetch & Merge Complete. Total rows: {df.shape[0]}")

        # 날짜 정렬 (계산을 위해 필수)
        df = df.sort_values(["symbol", "base_date"]).reset_index(drop=True)

        # 5. 피처 계산 (Feature Engineering)
        logger.info("Calculating technical and supply features...")
        
        feature_records = []
        available_at_str = generate_available_at_for_eod(target_date).isoformat()
        
        for symbol, group in df.groupby("symbol"):
            if symbol not in enabled_symbols:
                logger.debug(f"Skip symbol {symbol}: Not in enabled_symbols (stocks_master).")
                continue
                
            group = group.sort_values("base_date")
            
            # 가격 지표
            close = group["close_price"]
            group["return_5d"] = close.pct_change(5)
            group["moving_avg_5"] = close.rolling(5, min_periods=1).mean()
            group["moving_avg_20"] = close.rolling(20, min_periods=1).mean()
            # 20일 변동성 (수익률의 표준편차)
            group["volatility_20d"] = close.pct_change().rolling(20, min_periods=1).std()
            
            # 수급 지표 (외국인 순매수 Z-Score)
            foreign_buy = group["foreign_net_buy"]
            f_mean = foreign_buy.rolling(20, min_periods=1).mean()
            f_std = foreign_buy.rolling(20, min_periods=1).std()
            group["foreign_flow_zscore"] = (foreign_buy - f_mean) / f_std.replace(0, np.nan)
            
            # 마지막 날짜(target_date) 행 추출 (요구사항 1)
            last_row = group[group["base_date"] == target_pd_date]
            if last_row.empty:
                logger.debug(f"Symbol [{symbol}]: target_date [{target_date.strftime('%Y-%m-%d')}]에 해당하는 시세 데이터가 그룹 내에 존재하지 않음")
                continue
                
            row = last_row.iloc[0]
            
            # 결과물 리스트업
            targets = {
                "return_5d": row.get("return_5d"),
                "moving_avg_5": row.get("moving_avg_5"),
                "moving_avg_20": row.get("moving_avg_20"),
                "volatility_20d": row.get("volatility_20d"),
                "foreign_flow_zscore": row.get("foreign_flow_zscore"),
                "volume": row.get("volume")
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
                else:
                    logger.debug(f"Symbol [{symbol}]: 필요 데이터 20개 중 {len(group)}개만 존재하여 스킵됨 (Feature {f_name} is NaN)")
        # 7. 글로벌 매크로 피처 계산 (Copper/Gold, Gold/Oil Ratio)
        logger.info("Calculating global macro ratios...")
        macro_df = self._fetch_table_data("normalized_global_macro_daily", start_date_str, end_date_str)
        if not macro_df.empty:
            macro_df["base_date"] = pd.to_datetime(macro_df["base_date"]).dt.strftime('%Y-%m-%d')
            macro_df = macro_df.sort_values("base_date")
            # 금, 구리, 유성(wti) 컬럼 활용
            if "gold" in macro_df.columns and "copper" in macro_df.columns:
                macro_df["copper_gold_ratio"] = macro_df["copper"] / macro_df["gold"]
            if "gold" in macro_df.columns and "wti" in macro_df.columns:
                macro_df["gold_oil_ratio"] = macro_df["gold"] / macro_df["wti"]
            
            last_macro = macro_df[macro_df["base_date"] == target_pd_date]
            if not last_macro.empty:
                m_row = last_macro.iloc[0]
                for f_name in ["copper_gold_ratio", "gold_oil_ratio"]:
                    f_val = m_row.get(f_name)
                    if pd.notnull(f_val):
                        feature_records.append({
                            "symbol": "GLOBAL",
                            "base_date": end_date_str,
                            "feature_name": f_name,
                            "feature_value": float(f_val),
                            "available_at": available_at_str
                        })

        # 6. Feature Store 저장
        # 요구사항 3: 강제 확인 로직
        record_count = len(feature_records)
        logger.info(f"Target count to upsert: {record_count}")
        
        if record_count > 0:
            self.loader.upsert_records("feature_store_daily", feature_records)
            logger.info(f"Successfully upserted {record_count} real features for {target_date}.")
        else:
            logger.error(f"ERROR: No features calculated for the given date. Check if input data exists for {target_date}")

    def _fetch_table_data(self, table_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Supabase에서 특정 기간의 데이터를 DataFrame으로 로드"""
        try:
            # 자동 페이징 기능이 포함된 fetch_all 메서드 호출
            data = self.loader.fetch_all(table_name=table_name, 
                                         date_col="base_date", 
                                         start_date=start_date, 
                                         end_date=end_date, 
                                         order_col="base_date", 
                                         desc=True)
            
            df = pd.DataFrame(data)
            logger.info(f"Loaded {len(df)} rows from {table_name}")
            return df
        except Exception as e:
            logger.error(f"Error fetching {table_name}: {e}")
            return pd.DataFrame()

    def _load_universe(self):
        try:
            res = self.loader.client.table("stocks_master").select("symbol, name").eq("is_active", True).execute()
            if res.data:
                return res.data
            else:
                logger.warning("No active stocks found in stocks_master.")
                return []
        except Exception as e:
            logger.error(f"Error loading universe from DB: {e}")
            return []

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
