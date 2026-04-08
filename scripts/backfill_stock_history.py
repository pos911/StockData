import json
import time
from datetime import datetime, timedelta
from src.utils.config_loader import load_config
from src.collectors.kis_collector import KISCollector
from src.normalizers.stock_normalizer import StockNormalizer
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)

def load_universe():
    with open("config/stock_universe.json", "r", encoding="utf-8") as f:
        return json.load(f)

def backfill_stock_history():
    logger.info("Starting Historical Stock Data Backfill...")
    
    config = load_config()
    universe = load_universe()
    
    kis_config = config.get("kis", {})
    collector = KISCollector(
        api_key=kis_config.get("app_key", ""),
        api_secret=kis_config.get("app_secret", ""),
        account_no=kis_config.get("account_no", "")
    )
    
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    # 목표: 현재로부터 약 500영업일 (약 2년)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")
    available_at = datetime.now()

    enabled_stocks = [s for s in universe if s.get("enabled", False)]
    total_stocks = len(enabled_stocks)
    
    logger.info(f"Targeting {total_stocks} enabled stocks for backfill.")

    for i, stock in enumerate(enabled_stocks):
        symbol = stock["symbol"]
        name = stock["name"]
        market = stock["market"]
        
        logger.info(f"[{i+1}/{total_stocks}] Processing {name} ({symbol})...")
        
        # 1. Master 정보 업데이트
        master_data = StockNormalizer.normalize_stock_master(symbol, name, market)
        loader.upsert_records("stocks_master", [master_data])
        
        # 2. 과거 데이터 수집 (한 번에 100건씩 반환되므로 여러 번 호출 가능하지만, 여기선 대량 조회를 시도)
        # KIS 일자별 차트 API는 요청 기간 내 데이터를 최대 100건~1000건(권한별 상이) 반환함.
        # 일반 권한은 100건씩 끊어서 가져와야 함.
        
        all_history = []
        current_end_date = end_date
        
        # 최근 500건을 목표로 5회 반복 (100건씩 5번)
        for _ in range(5):
            raw_history = collector.fetch_ohlcv_history(symbol, start_date, current_end_date)
            if not raw_history:
                break
                
            all_history.extend(raw_history)
            
            if len(raw_history) < 100:
                break
                
            # 마지막 데이터의 날짜보다 하루 전을 새로운 end_date로 설정
            last_date_str = raw_history[-1].get("stck_bsop_date")
            if not last_date_str:
                break
            
            last_dt = datetime.strptime(last_date_str, "%Y%m%d")
            current_end_date = (last_dt - timedelta(days=1)).strftime("%Y%m%d")
            
            # API 가중치 조절을 위해 약간의 대기
            time.sleep(0.2)
        
        if all_history:
            # 3. 정규화 및 적재
            normalized_history = StockNormalizer.normalize_kis_history(all_history, symbol, available_at)
            
            # 중복 제거를 위해 고유 키 기준 정렬 및 유니크 확인은 upsert가 처리함
            logger.info(f"Upserting {len(normalized_history)} records for {symbol}...")
            loader.upsert_records("normalized_stock_prices_daily", normalized_history)
        else:
            logger.warning(f"No history found for {symbol}")

        # 종목 간 대기
        time.sleep(0.5)

    loader.insert_log("stock_history_backfill", end_date, "SUCCESS", total_stocks)
    logger.info("Backfill Process Finished.")

if __name__ == "__main__":
    backfill_stock_history()
