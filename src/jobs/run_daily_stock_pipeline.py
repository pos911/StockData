import os
import json
import uuid
import asyncio
from datetime import date
import argparse

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.collectors.krx_collector import KRXCollector
from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.fundamentals import KISFundamentalsCollector
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.collectors.opendart_collector import OpenDartCollector
from src.collectors.naver_news_collector import NaverNewsCollector
from src.normalizers.stock_normalizer import StockNormalizer
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config

logger = get_logger(__name__)

async def run_pipeline(target_date: date, limit: int = None):
    logger.info(f"Starting Modernized Daily Stock Pipeline for {target_date}...")
    
    config = load_config()
    
    # 1. Initialize Async Collectors
    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    
    semaphore = asyncio.Semaphore(2) # KIS TPS limit
    kis_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
    fundamentals_collector = KISFundamentalsCollector(config, auth_mgr, semaphore)
    
    # 2. Smart Universe Loading (Dynamic)
    universe_loader = DynamicUniverseLoader(config, kis_collector)
    universe = await universe_loader.get_combined_universe()
    
    if limit:
        logger.info(f"Limiting execution to first {limit} symbols for verification.")
        universe = universe[:limit]
    
    # Legacy Sync Collectors
    krx_auth_key = config.get("krx", {}).get("auth_key", "")
    krx_collector = KRXCollector(auth_key=krx_auth_key)
    
    opendart_collector = OpenDartCollector(api_key=config.get("opendart", {}).get("api_key", ""))
    naver_collector = NaverNewsCollector(
        client_id=config.get("naver", {}).get("client_id", ""),
        client_secret=config.get("naver", {}).get("client_secret", "")
    )
    
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    corp_code_map = opendart_collector.fetch_corp_code_map()
    
    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    total_processed = 0
    
    # 3. Collection Loop
    for stock in universe:
        symbol = stock["symbol"]
        name = stock["name"]
        
        try:
            logger.info(f"Processing {name} ({symbol})")
            
            # 4. stocks_master update
            master_data = StockNormalizer.normalize_stock_master(symbol, name, "DYNAMIC")
            loader.upsert_records("stocks_master", [master_data])

            # 5. KIS Data (Price, Supply, Short, Fundamentals)
            await kis_collector.fetch_ohlcv(symbol, timeframe='D', 
                                         start_date=target_date.strftime("%Y%m%d"), 
                                         end_date=target_date.strftime("%Y%m%d"),
                                         available_at=available_at.isoformat())
            
            await kis_collector.fetch_investor_trend(symbol, available_at=available_at.isoformat())
            await kis_collector.fetch_short_selling(symbol, available_at=available_at.isoformat())
            await fundamentals_collector.fetch_valuation_ratios(symbol, available_at=available_at.isoformat())
            await fundamentals_collector.fetch_financial_statements(symbol, available_at=available_at.isoformat())
            await fundamentals_collector.fetch_profitability_ratios(symbol, available_at=available_at.isoformat())
            
            # 6. OpenDart Disclosures
            corp_code = corp_code_map.get(symbol)
            if corp_code:
                disclosures = opendart_collector.fetch_daily_disclosures(corp_code, target_date.strftime("%Y-%m-%d"))
                if disclosures:
                    disclosure_records = []
                    for d in disclosures:
                        disclosure_records.append({
                            "source": "OpenDart",
                            "symbol": symbol,
                            "base_date": target_date.strftime("%Y-%m-%d"),
                            "raw_data": json.dumps(d),
                            "collected_at": timestamp_now.isoformat(),
                            "available_at": timestamp_now.isoformat()
                        })
                    loader.upsert_records("raw_disclosures", disclosure_records)
                    
                    # Event extraction
                    events = opendart_collector.parse_events(symbol, target_date.strftime("%Y-%m-%d"), disclosures)
                    if events:
                        event_records = []
                        for e in events:
                            event_records.append({
                                "symbol": e["symbol"],
                                "base_date": e["base_date"],
                                "event_type": e["event_type"],
                                "event_score": e["event_score"],
                                "sentiment_score": e["sentiment_score"],
                                "available_at": timestamp_now.isoformat()
                            })
                        if event_records:
                            loader.upsert_records("normalized_stock_events_daily", event_records)

            # 7. Naver News
            news_data = naver_collector.fetch_news(f"{name} {symbol}")
            if news_data and news_data.get("items"):
                news_records = []
                for item in news_data["items"]:
                    news_records.append({
                        "source": "NaverNews",
                        "symbol": symbol,
                        "base_date": target_date.strftime("%Y-%m-%d"),
                        "raw_data": json.dumps(item),
                        "collected_at": timestamp_now.isoformat(),
                        "available_at": timestamp_now.isoformat()
                    })
                loader.upsert_records("raw_disclosures", news_records)

            total_processed += 1
            
        except Exception as e:
            logger.error(f"Failed to process {symbol} ({name}): {e}", exc_info=True)
            continue

    # 8. Cleanup
    await KISBaseCollector.close_session()
    loader.insert_log("daily_stock_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", total_processed)
    logger.info("Pipeline Finished Successfully (Corrected Encoding).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    parser.add_argument("--limit", type=int, help="Limit number of symbols to process")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    asyncio.run(run_pipeline(target_dt, limit=args.limit))
