import asyncio
import os
import json
from datetime import date
from src.jobs.run_daily_stock_pipeline import run_pipeline
from src.utils.time_utils import get_current_kst
from src.utils.logger import get_logger
from src.utils.config_loader import load_config
from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.kis.fundamentals import KISFundamentalsCollector
from src.collectors.krx_collector import KRXCollector
from src.collectors.opendart_collector import OpenDartCollector
from src.collectors.naver_news_collector import NaverNewsCollector
from src.normalizers.stock_normalizer import StockNormalizer
from src.loaders.supabase_loader import SupabaseLoader
from src.collectors.kis.base import KISBaseCollector
from src.utils.time_utils import generate_available_at_for_eod

logger = get_logger(__name__)

async def test_single_symbol(symbol: str, name: str):
    logger.info(f"Testing integrated pipeline for {name} ({symbol})...")
    config = load_config()
    target_date = get_current_kst().date()
    
    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    
    semaphore = asyncio.Semaphore(2)
    kis_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
    fundamentals_collector = KISFundamentalsCollector(config, auth_mgr, semaphore)
    
    krx_auth_key = config.get("krx", {}).get("auth_key", "")
    krx_collector = KRXCollector(auth_key=krx_auth_key)
    
    opendart_collector = OpenDartCollector(api_key=config.get("opendart", {}).get("api_key", ""))
    naver_collector = NaverNewsCollector(
        client_id=config.get("naver", {}).get("client_id", ""),
        client_secret=config.get("naver", {}).get("client_secret", "")
    )
    
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    
    try:
        # 1. Master
        master_data = StockNormalizer.normalize_stock_master(symbol, name, "TEST")
        loader.upsert_records("stocks_master", [master_data])
        
        # 2. Price/Supply/Fundamentals/Short
        await kis_collector.fetch_ohlcv(symbol, timeframe='D', 
                                    start_date=target_date.strftime("%Y%m%d"), 
                                    end_date=target_date.strftime("%Y%m%d"),
                                    available_at=available_at.isoformat())
        await kis_collector.fetch_investor_trend(symbol, available_at=available_at.isoformat())
        await kis_collector.fetch_short_selling(symbol, available_at=available_at.isoformat())
        await fundamentals_collector.fetch_valuation_ratios(symbol, available_at=available_at.isoformat())
        await fundamentals_collector.fetch_financial_statements(symbol, available_at=available_at.isoformat())
        
        # 3. NEWS
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
            
        logger.info("Test single symbol finished successfully.")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
    finally:
        await KISBaseCollector.close_session()

if __name__ == "__main__":
    asyncio.run(test_single_symbol("005930", "삼성전자"))
