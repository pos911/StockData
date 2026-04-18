import os
import json
import uuid
import asyncio
from datetime import date
import argparse
import re

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

_NON_COMMON_NAME_PATTERNS = [
    r"ETF", r"ETN", r"인버스", r"레버리지", r"선물", r"액티브", r"머니마켓",
    r"우$", r"우B$", r"1우$", r"2우$", r"3우$"
]

def _canonical_symbol_key(symbol: str) -> str:
    """심볼 중복 제거를 위한 정규화 키 (Q-prefix alias 통합)."""
    if not symbol:
        return ""
    if symbol.startswith("Q") and len(symbol) > 1:
        return symbol[1:]
    return symbol

def _prefer_symbol(existing_symbol: str, new_symbol: str) -> str:
    """
    동일 canonical key에서 대표 심볼 선택.
    ETN/특수코드에서 Q-prefix를 우선 사용하여 API 호환성 확보.
    """
    if new_symbol and new_symbol.startswith("Q"):
        return new_symbol
    return existing_symbol or new_symbol

def _should_fetch_fundamentals(name: str) -> bool:
    """ETF/ETN/우선주 등 비대상 자산은 fundamentals 수집을 스킵."""
    if not name:
        return True
    for p in _NON_COMMON_NAME_PATTERNS:
        if re.search(p, name):
            return False
    return True

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
    
    # Ensure all active stocks in stocks_master are included
    try:
        res = loader.client.table("stocks_master").select("symbol, name").eq("is_active", True).execute()
        active_stocks = res.data if res.data else []
        universe_keys = {_canonical_symbol_key(u["symbol"]) for u in universe}
        for s in active_stocks:
            if _canonical_symbol_key(s["symbol"]) not in universe_keys:
                universe.append({"symbol": s["symbol"], "name": s["name"], "source_category": "active_master"})
                logger.info(f"Added manual active stock from DB: {s['name']} ({s['symbol']})")
    except Exception as e:
        logger.error(f"Failed to load active stocks from master: {e}")

    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    total_processed = 0

    # 유니버스 중복 제거 (symbol 기준)
    canonical_map = {}
    for s in universe:
        sym = s["symbol"]
        key = _canonical_symbol_key(sym)
        if key not in canonical_map:
            canonical_map[key] = dict(s)
        else:
            chosen_symbol = _prefer_symbol(canonical_map[key].get("symbol"), sym)
            canonical_map[key]["symbol"] = chosen_symbol
            # name/source_category는 최초값 유지 (추후 필요 시 병합 가능)
    universe = list(canonical_map.values())

    if limit:
        logger.info(f"Limiting execution to first {limit} symbols for verification.")
        universe = universe[:limit]

    logger.info(f"Universe after dedup: {len(universe)} symbols")

    # 배치 버퍼 (동적 Flush 포함)
    price_buf: list = []
    supply_buf: list = []
    ratio_buf: list = []
    master_buf: list = []
    seen_master: set = set()  # master_buf 중복 방지
    FLUSH_SIZE = 200

    def flush_buf(buf: list, table: str):
        """버퍼가 FLUSH_SIZE 이상이면 즉시 upsert 후 비우기"""
        if len(buf) >= FLUSH_SIZE:
            loader.upsert_records(table, buf[:])
            buf.clear()
            logger.info(f"[DynamicFlush] {table} flushed mid-loop.")
    
    # 3. Collection Loop
    for stock in universe:
        symbol = stock["symbol"]
        name = stock["name"]
        
        try:
            logger.info(f"Processing {name} ({symbol}) [Sources: {source_cat}]")
            
            # 4. stocks_master 업데이트
            master_data = StockNormalizer.normalize_stock_master(symbol, name, "DYNAMIC")
            loader.upsert_records("stocks_master", [master_data])

            # 5. KIS 데이터 수집 (Async)
            kis_ohlcv = await kis_collector.fetch_ohlcv(
                symbol,
                timeframe='D',
                start_date=target_date.strftime("%Y%m%d"),
                end_date=target_date.strftime("%Y%m%d")
            )
            
            # 6. 수급 데이터 수집 (Async)
            supply_records = await kis_collector.fetch_investor_trend(symbol)

            # 6-1. KIS 시세 정규화/적재 (fallback: KRX보다 우선)
            if kis_ohlcv:
                latest_kis = sorted(kis_ohlcv, key=lambda x: x.get("base_date", ""), reverse=True)[0]
                normalized_kis_price = {
                    "symbol": symbol,
                    "base_date": f"{latest_kis['base_date'][:4]}-{latest_kis['base_date'][4:6]}-{latest_kis['base_date'][6:8]}",
                    "open_price": float(latest_kis.get("open", 0)),
                    "high_price": float(latest_kis.get("high", 0)),
                    "low_price": float(latest_kis.get("low", 0)),
                    "close_price": float(latest_kis.get("close", 0)),
                    "volume": float(latest_kis.get("volume", 0)),
                    "trading_value": float(latest_kis.get("trading_value", 0)),
                    "market_cap": None,
                    "outstanding_shares": None,
                    "available_at": available_at.isoformat()
                }
                loader.upsert_records("normalized_stock_prices_daily", [normalized_kis_price])

            # 6-2. KIS 수급 정규화/적재
            if supply_records:
                normalized_supply = [
                    StockNormalizer.normalize_kis_supply(row, symbol, available_at)
                    for row in supply_records
                ]
                loader.upsert_records("normalized_stock_supply_daily", normalized_supply)
            logger.info(f"Processing {name} ({symbol})")

            # --- 신규 편입 종목 자동 백필 (배치 버퍼 방식) ---
            try:
                from datetime import timedelta as _td
                _count_res = loader.client.table("normalized_stock_prices_daily") \
                    .select("base_date", count="exact") \
                    .eq("symbol", symbol) \
                    .execute()
                _hist_count = _count_res.count if hasattr(_count_res, 'count') and _count_res.count else len(_count_res.data or [])
                if _hist_count < 20:
                    logger.info(f"[Backfill] {symbol} has only {_hist_count} days. Buffering 30-day backfill...")
                    _bf_start = (target_date - _td(days=35)).strftime("%Y%m%d")
                    _bf_end = (target_date - _td(days=1)).strftime("%Y%m%d")

                    # a) OHLCV 30일치 -> price_buf
                    _bf_prices = await kis_collector.fetch_ohlcv(
                        symbol, timeframe='D',
                        start_date=_bf_start, end_date=_bf_end,
                        available_at=available_at.isoformat()
                    )
                    if _bf_prices:
                        price_buf.extend(_bf_prices)
                    await asyncio.sleep(0.2)

                    # b) 투자자 동향 30일치 -> supply_buf
                    _bf_supply = await kis_collector.fetch_investor_trend(symbol, available_at=available_at.isoformat())
                    if _bf_supply:
                        supply_buf.extend(_bf_supply)
                    await asyncio.sleep(0.2)

                    # c) 기본정보(시가총액/PER/PBR 등) -> ratio_buf
                    _bf_ratio = await kis_collector.fetch_fundamental_info(
                        symbol, base_date=target_date.strftime("%Y-%m-%d"),
                        available_at=available_at.isoformat()
                    )
                    if _bf_ratio:
                        ratio_buf.append(_bf_ratio)
                    await asyncio.sleep(0.2)

                    logger.info(f"[Backfill] {symbol} buffered.")

                    # 동적 Flush (200건 단위)
                    flush_buf(price_buf, "normalized_stock_prices_daily")
                    flush_buf(supply_buf, "normalized_stock_supply_daily")
                    flush_buf(ratio_buf, "normalized_stock_fundamentals_ratios")
            except Exception as _bf_err:
                logger.warning(f"[Backfill] {symbol} backfill failed (non-fatal): {_bf_err}")

            # 4. stocks_master update (중복 방어)
            if symbol not in seen_master:
                master_data = StockNormalizer.normalize_stock_master(symbol, name, "DYNAMIC")
                master_buf.append(master_data)
                seen_master.add(symbol)
                flush_buf(master_buf, "stocks_master")

            # 5. KIS Data (Price, Supply, Short, Fundamentals)
            await kis_collector.fetch_ohlcv(symbol, timeframe='D', 
                                         start_date=target_date.strftime("%Y%m%d"), 
                                         end_date=target_date.strftime("%Y%m%d"),
                                         available_at=available_at.isoformat())
            
            await kis_collector.fetch_investor_trend(symbol, available_at=available_at.isoformat())
            await kis_collector.fetch_short_selling(symbol, available_at=available_at.isoformat())
            if _should_fetch_fundamentals(name):
                _base_date = target_date.strftime("%Y-%m-%d")
                await fundamentals_collector.fetch_valuation_ratios(symbol, base_date=_base_date, available_at=available_at.isoformat())
                await fundamentals_collector.fetch_financial_statements(symbol, available_at=available_at.isoformat())
                await fundamentals_collector.fetch_profitability_ratios(symbol, base_date=_base_date, available_at=available_at.isoformat())
            else:
                logger.info(f"Skip fundamentals for non-common asset: {name} ({symbol})")
            
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

            # 9. KRX 데이터 수집 (Sync, KIS 미수집 시 fallback)
            if not kis_ohlcv:
                raw_data = krx_collector.fetch_daily_ohlcv(symbol, target_date)
                if raw_data:
                    norm_data = StockNormalizer.normalize_krx_daily(raw_data, available_at)
                    loader.upsert_records("normalized_stock_prices_daily", [norm_data])

            total_processed += 1
            
        except Exception as e:
            logger.error(f"Failed to process {symbol} ({name}): {e}", exc_info=True)
            continue

    # 8. 배치 버퍼 최종 Flush (잔여 데이터 일괄 적재)
    logger.info(f"Final flush: price={len(price_buf)}, supply={len(supply_buf)}, ratio={len(ratio_buf)}, master={len(master_buf)}")
    if price_buf:
        loader.upsert_records("normalized_stock_prices_daily", price_buf)
    if supply_buf:
        loader.upsert_records("normalized_stock_supply_daily", supply_buf)
    if ratio_buf:
        loader.upsert_records("normalized_stock_fundamentals_ratios", ratio_buf)
    if master_buf:
        loader.upsert_records("stocks_master", master_buf)

    # 9. 오늘 자 데이터 적재 여부 감시 (CRITICAL 경보)
    today_str = target_date.strftime("%Y-%m-%d")
    watch_tables = [
        "normalized_stock_prices_daily",
        "normalized_stock_supply_daily",
        "normalized_macro_series",
        "feature_store_daily",
    ]
    for _tbl in watch_tables:
        try:
            _res = loader.client.table(_tbl).select("base_date", count="exact").eq("base_date", today_str).limit(1).execute()
            _cnt = _res.count if hasattr(_res, 'count') and _res.count else len(_res.data or [])
            if _cnt == 0:
                logger.critical(f"CRITICAL: No data loaded for [{_tbl}] on {today_str}")
        except Exception as _we:
            logger.warning(f"Watch check failed for {_tbl}: {_we}")

    # 10. Cleanup
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
