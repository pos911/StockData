import json
import time
from datetime import datetime, timedelta
from src.utils.logger import get_logger
from src.utils.config_loader import load_config
from src.loaders.supabase_loader import SupabaseLoader
from src.collectors.kis_collector import KISCollector
import FinanceDataReader as fdr

logger = get_logger(__name__)

def backfill_new_stocks():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    kis_collector = KISCollector(config=config)
    
    # Newly added stocks
    new_stocks = [
        {"symbol": "017670", "name": "SK텔레콤"},
        {"symbol": "222800", "name": "심텍"},
        {"symbol": "004020", "name": "현대제철"}
    ]
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    for stock in new_stocks:
        symbol = stock["symbol"]
        name = stock["name"]
        logger.info(f"Targeted backfill for {name} ({symbol})...")
        
        # 1. Price History
        try:
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
            logger.error(f"Error price backfill for {symbol}: {e}")

        # 2. Supply History
        try:
            history = kis_collector.fetch_supply_history(symbol)
            if history:
                supply_records = []
                for item in history:
                    item_dt = datetime.strptime(item["base_date"], "%Y%m%d").date()
                    if start_date.date() <= item_dt <= end_date.date():
                        dt_str = item_dt.strftime("%Y-%m-%d")
                        available_at = datetime.combine(item_dt, datetime.min.time().replace(hour=18)).isoformat()
                        supply_records.append({
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
                if supply_records:
                    loader.upsert_records("normalized_stock_supply_daily", supply_records)
                    logger.info(f"Upserted {len(supply_records)} supply records for {symbol}")
        except Exception as e:
            logger.error(f"Error supply backfill for {symbol}: {e}")
            
        time.sleep(1)

    logger.info("Targeted backfill complete.")

if __name__ == "__main__":
    backfill_new_stocks()
