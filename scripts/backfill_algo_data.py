import os
import json
import time
from datetime import datetime, timedelta
from typing import List

from src.utils.logger import get_logger
from src.utils.config_loader import load_config
from src.loaders.supabase_loader import SupabaseLoader
from src.collectors.krx_collector import KRXCollector
from src.collectors.global_index_collector import GlobalIndexCollector

logger = get_logger(__name__)

def load_universe():
    with open("config/stock_universe.json", "r", encoding="utf-8") as f:
        return json.load(f)

def backfill_algo_data(days: int = 365):
    logger.info(f"Starting Historical Algo Data Backfill for the last {days} days...")
    
    config = load_config()
    universe = load_universe()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    krx_collector = KRXCollector()
    global_collector = GlobalIndexCollector()
    
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    
    current_date = start_date
    
    while current_date <= end_date:
        dt_str = current_date.strftime("%Y-%m-%d")
        # available_at: Assume data is available at 18:00 KST of that day
        available_at = datetime.combine(current_date, datetime.min.time().replace(hour=18)).isoformat()
        
        logger.info(f"Processing date: {dt_str}")
        
        # 1. Macro Data Backfill
        try:
            macro_data = global_collector.fetch_daily_indices(current_date)
            if macro_data:
                macro_record = macro_data.copy()
                macro_record["available_at"] = available_at
                loader.upsert_records("normalized_global_macro_daily", [macro_record])
        except Exception as e:
            logger.error(f"Error backfilling macro for {dt_str}: {e}")

        # 2. Stock Supply Backfill
        enabled_stocks = [s for s in universe if s.get("enabled", False)]
        for stock in enabled_stocks:
            symbol = stock["symbol"]
            try:
                supply_data = krx_collector.fetch_daily_investor_supply(symbol, current_date)
                if supply_data:
                    norm_supply = {
                        "symbol": symbol,
                        "base_date": dt_str,
                        "foreign_net_buy": supply_data.get("foreign_net_buy"),
                        "institutional_net_buy": supply_data.get("institutional_net_buy"),
                        "individual_net_buy": supply_data.get("individual_net_buy"),
                        "foreign_holding_ratio": supply_data.get("foreign_holding_ratio"),
                        "short_volume": supply_data.get("short_volume"),
                        "short_balance": supply_data.get("short_balance"),
                        "lending_balance": supply_data.get("lending_balance"),
                        "available_at": available_at
                    }
                    loader.upsert_records("normalized_stock_supply_daily", [norm_supply])
            except Exception as e:
                logger.error(f"Error backfilling supply for {symbol} on {dt_str}: {e}")
            
            # Rate limiting prevention
            time.sleep(0.1)

        current_date += timedelta(days=1)
        time.sleep(0.5)

    logger.info("Backfill Finished.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365, help="Number of days to backfill")
    args = parser.parse_args()
    
    backfill_algo_data(days=args.days)
