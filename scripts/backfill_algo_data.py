import os
import json
import time
from datetime import datetime, timedelta
from typing import List

from src.utils.logger import get_logger
from src.utils.config_loader import load_config
from src.loaders.supabase_loader import SupabaseLoader
from src.collectors.kis_collector import KISCollector
from src.collectors.global_index_collector import GlobalIndexCollector
from src.normalizers.stock_normalizer import StockNormalizer

logger = get_logger(__name__)

def load_universe():
    with open("config/stock_universe.json", "r", encoding="utf-8") as f:
        return json.load(f)

def backfill_algo_data(days: int = 365):
    logger.info(f"Starting Historical Algo Data Backfill for the last {days} days...")
    
    config = load_config()
    universe = load_universe()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    kis_collector = KISCollector(config=config)
    global_collector = GlobalIndexCollector()
    
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    
    # 1. Stock Supply Backfill (Stock-centric Bulk Load)
    # Optimized: Fetch all 40 pages of history once per stock
    enabled_stocks = [s for s in universe if s.get("enabled", False)]
    for stock in enabled_stocks:
        symbol = stock["symbol"]
        name = stock["name"]
        logger.info(f"Processing backfill for {name} ({symbol})...")
        
        # 1.1 Price History Backfill (FinanceDataReader)
        try:
            import FinanceDataReader as fdr
            logger.info(f"Fetching price history for {symbol} via FDR...")
            df = fdr.DataReader(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            if not df.empty:
                records = []
                for idx, row in df.iterrows():
                    dt_str = idx.strftime("%Y-%m-%d")
                    available_at = datetime.combine(idx.date(), datetime.min.time().replace(hour=18)).isoformat()
                    records.append({
                        "symbol": symbol,
                        "base_date": dt_str,
                        "open_price": float(row.get("Open", 0)),
                        "high_price": float(row.get("High", 0)),
                        "low_price": float(row.get("Low", 0)),
                        "close_price": float(row.get("Close", 0)),
                        "volume": float(row.get("Volume", 0)),
                        "trading_value": float(row.get("Amount", 0)) if "Amount" in row else 0,
                        "available_at": available_at
                    })
                loader.upsert_records("normalized_stock_prices_daily", records)
                logger.info(f"Upserted {len(records)} price records for {symbol}")
        except Exception as e:
            logger.error(f"Error backfilling price for {symbol}: {e}")

        # 1.2 Stock Supply Backfill (KIS API)
        logger.info(f"Bulk backfilling supply history for {symbol}...")
        try:
            # Scrape recent history from KIS API
            history = kis_collector.fetch_supply_history(symbol)
            if history:
                records = []
                for item in history:
                    # Filter by date range
                    item_dt = datetime.strptime(item["base_date"], "%Y%m%d").date()
                    if start_date <= item_dt <= end_date:
                        dt_str = item_dt.strftime("%Y-%m-%d")
                        available_at = datetime.combine(item_dt, datetime.min.time().replace(hour=18)).isoformat()
                        
                        records.append({
                            "symbol": symbol,
                            "base_date": dt_str,
                            "foreign_net_buy": item.get("foreign_net_buy"),
                            "institutional_net_buy": item.get("institutional_net_buy"),
                            "individual_net_buy": item.get("individual_net_buy"),
                            "foreign_holding_ratio": item.get("foreign_holding_ratio"),
                            "short_volume": item.get("short_volume"),
                            "short_balance": item.get("short_balance"),
                            "lending_balance": item.get("lending_balance"),
                            "available_at": available_at
                        })
                
                if records:
                    loader.upsert_records("normalized_stock_supply_daily", records)
                    logger.info(f"Upserted {len(records)} supply records for {symbol}")
        except Exception as e:
            logger.error(f"Error bulk backfilling supply for {symbol}: {e}")
        
        time.sleep(1.0) # Respectful delay between stocks

    # 2. Macro Data Backfill (Date-centric is fine for YF)
    current_date = start_date
    while current_date <= end_date:
        dt_str = current_date.strftime("%Y-%m-%d")
        available_at = datetime.combine(current_date, datetime.min.time().replace(hour=18)).isoformat()
        
        logger.info(f"Processing macro for date: {dt_str}")
        try:
            macro_data = global_collector.fetch_daily_indices(current_date)
            if macro_data:
                macro_record = macro_data.copy()
                macro_record["available_at"] = available_at
                loader.upsert_records("normalized_global_macro_daily", [macro_record])
        except Exception as e:
            logger.error(f"Error backfilling macro for {dt_str}: {e}")
        
        current_date += timedelta(days=1)
        time.sleep(0.1)

    logger.info("Backfill Finished.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365, help="Number of days to backfill")
    args = parser.parse_args()
    
    backfill_algo_data(days=args.days)
