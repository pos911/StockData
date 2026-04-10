import os
import json
import uuid
from datetime import date
import argparse

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.collectors.krx_collector import KRXCollector
from src.collectors.kis_collector import KISCollector
from src.collectors.opendart_collector import OpenDartCollector
from src.collectors.naver_news_collector import NaverNewsCollector
from src.normalizers.stock_normalizer import StockNormalizer
from src.loaders.supabase_loader import SupabaseLoader

logger = get_logger(__name__)

from src.utils.config_loader import load_config

def load_universe():
    with open("config/stock_universe.json", "r", encoding="utf-8") as f:
        return json.load(f)

def run_pipeline(target_date: date):
    logger.info(f"Starting Daily Stock Pipeline for {target_date}...")
    
    config = load_config()
    universe = load_universe()
    
    # 1. 초기화
    krx_auth_key = config.get("krx", {}).get("auth_key", "")
    skip_krx = not krx_auth_key or "YOUR_KRX_AUTH_KEY" in krx_auth_key
    
    krx_collector = KRXCollector(auth_key=krx_auth_key)
    
    kis_config = config.get("kis", {})
    kis_collector = KISCollector(
        api_key=kis_config.get("app_key", ""),
        api_secret=kis_config.get("app_secret", ""),
        account_no=kis_config.get("account_no", "")
    )
    
    opendart_collector = OpenDartCollector(api_key=config.get("opendart", {}).get("api_key", ""))
    naver_collector = NaverNewsCollector(
        client_id=config.get("naver", {}).get("client_id", ""),
        client_secret=config.get("naver", {}).get("client_secret", "")
    )
    
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    # 0. OpenDart 고유번호 매핑 준비
    corp_code_map = opendart_collector.fetch_corp_code_map()
    
    if skip_krx:
        logger.warning("KRX Auth Key가 설정되지 않았거나 기본값입니다. KRX 수집을 건너뜁합니다.")
    
    # 2. 실행 시점
    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()

    total_processed = 0
    
    # 3. 유니버스 기반 순회 수집
    for stock in universe:
        if not stock.get("enabled", False):
            continue
            
        symbol = stock["symbol"]
        name = stock["name"]
        market = stock["market"]
        
        # 4. stocks_master 업데이트 (KIS/KRX 공통 메타 정보)
        master_data = StockNormalizer.normalize_stock_master(symbol, name, market)
        loader.upsert_records("stocks_master", [master_data])

        # 5. KIS 데이터 수집 (Raw & Normalized)
        kis_raw = kis_collector.fetch_stock_price(symbol)
        if kis_raw:
            # Normalized 주가 적재
            kis_norm = StockNormalizer.normalize_kis_price(kis_raw, available_at)
            loader.upsert_records("normalized_stock_prices_daily", [kis_norm])
            total_processed += 1

        # 6. OpenDart 공시 수집 및 이벤트 추출
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
                
                # 이벤트 추출 및 적재
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

        # 7. Naver 뉴스 수집 (Raw 레이어의 disclosures와 통합 저장하거나 별도 관리 가능)
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

        # 8. KRX 데이터 수집 (Raw & Normalized & 수급)
        if skip_krx:
            continue
            
        raw_data = krx_collector.fetch_daily_ohlcv(symbol, target_date)
        if not raw_data:
            logger.warning(f"No KRX data for {symbol} on {target_date}, skipping.")
            continue
            
        # Raw DB 적재
        raw_record = {
            "source": "KRX",
            "symbol": symbol,
            "base_date": target_date.strftime("%Y-%m-%d"),
            "raw_data": json.dumps(raw_data),
            "collected_at": timestamp_now.isoformat(),
            "available_at": available_at.isoformat()
        }
        loader.upsert_records("raw_stock_prices_daily", [raw_record])
        
        # Normalized 주가 적재 (KRX 기준)
        norm_data = StockNormalizer.normalize_krx_daily(raw_data, available_at)
        loader.upsert_records("normalized_stock_prices_daily", [norm_data])
        
        # 수급 데이터 (Supply) 처리
        supply_data = krx_collector.fetch_daily_investor_supply(symbol, target_date)
        if supply_data:
            # Raw Supply
            raw_supply_record = {
                "source": "KRX_Supply",
                "symbol": symbol,
                "base_date": target_date.strftime("%Y-%m-%d"),
                "raw_data": json.dumps(supply_data),
                "collected_at": timestamp_now.isoformat(),
                "available_at": available_at.isoformat()
            }
            loader.upsert_records("raw_stock_supply_daily", [raw_supply_record])
            
            # Normalized Supply
            norm_supply = {
                "symbol": symbol,
                "base_date": target_date.strftime("%Y-%m-%d"),
                "foreign_net_buy": supply_data.get("foreign_net_buy"),
                "institutional_net_buy": supply_data.get("institutional_net_buy"),
                "individual_net_buy": supply_data.get("individual_net_buy"),
                "foreign_holding_ratio": supply_data.get("foreign_holding_ratio"),
                "short_volume": supply_data.get("short_volume"),
                "short_balance": supply_data.get("short_balance"),
                "lending_balance": supply_data.get("lending_balance"),
                "available_at": available_at.isoformat()
            }
            loader.upsert_records("normalized_stock_supply_daily", [norm_supply])

        total_processed += 1

    # 6. 로깅
    loader.insert_log("daily_stock_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", total_processed)
    logger.info("Pipeline Finished Successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_pipeline(target_dt)
