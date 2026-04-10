import os
import argparse
from datetime import date, timedelta
from typing import List, Dict, Any

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config

logger = get_logger(__name__)

class FeatureGenerator:
    """
    Normalized 데이터를 바탕으로 Trading Algorithmic Feature들을 생성.
    실제 프로덕션에서는 pandas/dask 기반으로 윈도우 함수 및 rolling 등을 적용해야 하지만
    현 스크립트는 뼈대를 나타냅니다.
    """
    def __init__(self, loader: SupabaseLoader):
        self.loader = loader

    def generate_features_for_date(self, target_date: date):
        # 파라미터 준비
        base_date_str = target_date.strftime("%Y-%m-%d")
        available_at_str = generate_available_at_for_eod(target_date).isoformat()
        
        feature_records = []
        
        # 1. Price-based & Supply-based
        # (원래 DB에서 select해서 pandas에서 일괄 계산하지만, 목업으로 구성)
        symbols_to_process = ["005930", "000660"]
        for symbol in symbols_to_process:
            # Price Mock
            feature_records.extend([
                self._record(symbol, base_date_str, "return_5d", 0.02, available_at_str),
                self._record(symbol, base_date_str, "return_20d", -0.015, available_at_str),
                self._record(symbol, base_date_str, "moving_avg_5", 52000, available_at_str),
                self._record(symbol, base_date_str, "moving_avg_20", 51500, available_at_str),
                self._record(symbol, base_date_str, "moving_avg_60", 50000, available_at_str),
                self._record(symbol, base_date_str, "volatility_20d", 0.01, available_at_str),
                self._record(symbol, base_date_str, "volume_spike_ratio", 1.5, available_at_str),
            ])
            # Supply Mock
            feature_records.extend([
                self._record(symbol, base_date_str, "foreign_flow_zscore", 1.2, available_at_str),
                self._record(symbol, base_date_str, "institutional_flow_zscore", -0.5, available_at_str),
                self._record(symbol, base_date_str, "accumulation_score", 0.8, available_at_str),
            ])
            # Event Mock
            feature_records.extend([
                self._record(symbol, base_date_str, "event_score", 0.5, available_at_str),
                self._record(symbol, base_date_str, "sentiment_score", 0.3, available_at_str),
            ])

        # 2. Macro-based & Derivatives Mock (applied to general "MARKET")
        feature_records.extend([
            self._record("MARKET", base_date_str, "usdkrw_momentum", -0.01, available_at_str),
            self._record("MARKET", base_date_str, "us10y_change", 0.05, available_at_str),
            self._record("MARKET", base_date_str, "risk_on_off_score", 0.6, available_at_str),
            self._record("MARKET", base_date_str, "basis_signal", 1, available_at_str), # 1: contango, -1: backwardation
            self._record("MARKET", base_date_str, "oi_change_signal", 1.1, available_at_str),
            self._record("MARKET", base_date_str, "expiration_effect_flag", 0, available_at_str),
        ])

        # Feature Store 저장
        if feature_records:
            self.loader.upsert_records("feature_store_daily", feature_records)
            logger.info(f"Upserted {len(feature_records)} features into feature_store_daily.")

    def _record(self, symbol, date_str, f_name, f_value, available_at):
        return {
            "symbol": symbol,
            "base_date": date_str,
            "feature_name": f_name,
            "feature_value": f_value,
            "available_at": available_at
        }

def run_job(target_date: date):
    logger.info(f"Starting Feature Engineering Job for {target_date}...")
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
