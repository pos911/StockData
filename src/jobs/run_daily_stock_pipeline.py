import asyncio
import json
import argparse
import re
from datetime import date, timedelta

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
    r"ETF",
    r"ETN",
    r"SPAC",
    r"REIT",
    r"Preferred",
    r"우$",
    r"우B$",
    r"1우$",
    r"2우$",
    r"3우$",
]


def _canonical_symbol_key(symbol: str) -> str:
    if not symbol:
        return ""
    if symbol.startswith("Q") and len(symbol) > 1:
        return symbol[1:]
    return symbol


def _prefer_symbol(existing_symbol: str, new_symbol: str) -> str:
    if new_symbol and new_symbol.startswith("Q"):
        return new_symbol
    return existing_symbol or new_symbol


def _should_fetch_fundamentals(name: str) -> bool:
    if not name:
        return True
    return not any(re.search(pattern, name) for pattern in _NON_COMMON_NAME_PATTERNS)


async def run_pipeline(target_date: date, limit: int = None):
    logger.info(f"Starting Modernized Daily Stock Pipeline for {target_date}...")

    config = load_config()

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()

    semaphore = asyncio.Semaphore(2)
    kis_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
    fundamentals_collector = KISFundamentalsCollector(config, auth_mgr, semaphore)

    universe_loader = DynamicUniverseLoader(config, kis_collector)
    universe = await universe_loader.get_combined_universe()

    krx_collector = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    opendart_collector = OpenDartCollector(api_key=config.get("opendart", {}).get("api_key", ""))
    naver_collector = NaverNewsCollector(
        client_id=config.get("naver", {}).get("client_id", ""),
        client_secret=config.get("naver", {}).get("client_secret", ""),
    )

    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    corp_code_map = opendart_collector.fetch_corp_code_map()

    try:
        res = loader.client.table("stocks_master").select("symbol, name").eq("is_active", True).execute()
        active_stocks = res.data if res.data else []
        universe_keys = {_canonical_symbol_key(item["symbol"]) for item in universe}
        for stock in active_stocks:
            if _canonical_symbol_key(stock["symbol"]) not in universe_keys:
                universe.append(
                    {
                        "symbol": stock["symbol"],
                        "name": stock["name"],
                        "source_category": "active_master",
                    }
                )
                logger.info(f"Added manual active stock from DB: {stock['name']} ({stock['symbol']})")
    except Exception as exc:
        logger.error(f"Failed to load active stocks from master: {exc}")

    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    total_processed = 0

    canonical_map = {}
    for stock in universe:
        symbol = stock["symbol"]
        key = _canonical_symbol_key(symbol)
        if key not in canonical_map:
            canonical_map[key] = dict(stock)
        else:
            canonical_map[key]["symbol"] = _prefer_symbol(canonical_map[key].get("symbol"), symbol)
    universe = list(canonical_map.values())

    if limit:
        logger.info(f"Limiting execution to first {limit} symbols for verification.")
        universe = universe[:limit]

    logger.info(f"Universe after dedup: {len(universe)} symbols")

    price_buf = []
    supply_buf = []
    ratio_buf = []
    master_buf = []
    seen_master = set()
    flush_size = 200

    def flush_buf(buf: list, table: str):
        if len(buf) >= flush_size:
            loader.upsert_records(table, buf[:])
            buf.clear()
            logger.info(f"[DynamicFlush] {table} flushed mid-loop.")

    for stock in universe:
        symbol = stock["symbol"]
        name = stock["name"]
        source_cat = stock.get("source_category", "unknown")

        try:
            logger.info(f"Processing {name} ({symbol}) [Sources: {source_cat}]")

            kis_ohlcv = await kis_collector.fetch_ohlcv(
                symbol,
                timeframe="D",
                start_date=target_date.strftime("%Y%m%d"),
                end_date=target_date.strftime("%Y%m%d"),
            )
            supply_records = await kis_collector.fetch_investor_trend(symbol)
            logger.info(
                f"Collected {len(kis_ohlcv)} price rows and {len(supply_records)} supply rows for {name} ({symbol})"
            )

            try:
                count_res = (
                    loader.client.table("normalized_stock_prices_daily")
                    .select("base_date", count="exact")
                    .eq("symbol", symbol)
                    .execute()
                )
                hist_count = count_res.count if getattr(count_res, "count", None) else len(count_res.data or [])
                if hist_count < 20:
                    logger.info(f"[Backfill] {symbol} has only {hist_count} days. Buffering 30-day backfill...")
                    bf_start = (target_date - timedelta(days=35)).strftime("%Y%m%d")
                    bf_end = (target_date - timedelta(days=1)).strftime("%Y%m%d")

                    bf_prices = await kis_collector.fetch_ohlcv(
                        symbol,
                        timeframe="D",
                        start_date=bf_start,
                        end_date=bf_end,
                        available_at=available_at.isoformat(),
                    )
                    if bf_prices:
                        price_buf.extend(bf_prices)
                    await asyncio.sleep(0.2)

                    bf_supply = await kis_collector.fetch_investor_trend(
                        symbol,
                        available_at=available_at.isoformat(),
                    )
                    if bf_supply:
                        supply_buf.extend(bf_supply)
                    await asyncio.sleep(0.2)

                    bf_ratio = await kis_collector.fetch_fundamental_info(
                        symbol,
                        base_date=target_date.strftime("%Y-%m-%d"),
                        available_at=available_at.isoformat(),
                    )
                    if bf_ratio:
                        ratio_buf.append(bf_ratio)
                    await asyncio.sleep(0.2)

                    logger.info(f"[Backfill] {symbol} buffered.")
                    flush_buf(price_buf, "normalized_stock_prices_daily")
                    flush_buf(supply_buf, "normalized_stock_supply_daily")
                    flush_buf(ratio_buf, "normalized_stock_fundamentals_ratios")
            except Exception as backfill_exc:
                logger.warning(f"[Backfill] {symbol} backfill failed (non-fatal): {backfill_exc}")

            if symbol not in seen_master:
                master_buf.append(StockNormalizer.normalize_stock_master(symbol, name, "DYNAMIC"))
                seen_master.add(symbol)
                flush_buf(master_buf, "stocks_master")

            await kis_collector.fetch_short_selling(symbol, available_at=available_at.isoformat())
            if _should_fetch_fundamentals(name):
                base_date_str = target_date.strftime("%Y-%m-%d")
                await fundamentals_collector.fetch_valuation_ratios(
                    symbol,
                    base_date=base_date_str,
                    available_at=available_at.isoformat(),
                )
                await fundamentals_collector.fetch_financial_statements(
                    symbol,
                    available_at=available_at.isoformat(),
                )
                await fundamentals_collector.fetch_profitability_ratios(
                    symbol,
                    base_date=base_date_str,
                    available_at=available_at.isoformat(),
                )
            else:
                logger.info(f"Skip fundamentals for non-common asset: {name} ({symbol})")

            corp_code = corp_code_map.get(symbol)
            if corp_code:
                disclosures = opendart_collector.fetch_daily_disclosures(corp_code, target_date.strftime("%Y-%m-%d"))
                if disclosures:
                    disclosure_records = [
                        {
                            "source": "OpenDart",
                            "symbol": symbol,
                            "base_date": target_date.strftime("%Y-%m-%d"),
                            "raw_data": json.dumps(item),
                            "collected_at": timestamp_now.isoformat(),
                            "available_at": timestamp_now.isoformat(),
                        }
                        for item in disclosures
                    ]
                    loader.upsert_records("raw_disclosures", disclosure_records)

                    events = opendart_collector.parse_events(symbol, target_date.strftime("%Y-%m-%d"), disclosures)
                    if events:
                        event_records = [
                            {
                                "symbol": event["symbol"],
                                "base_date": event["base_date"],
                                "event_type": event["event_type"],
                                "event_score": event["event_score"],
                                "sentiment_score": event["sentiment_score"],
                                "available_at": timestamp_now.isoformat(),
                            }
                            for event in events
                        ]
                        if event_records:
                            loader.upsert_records("normalized_stock_events_daily", event_records)

            news_data = naver_collector.fetch_news(f"{name} {symbol}")
            if news_data and news_data.get("items"):
                news_records = [
                    {
                        "source": "NaverNews",
                        "symbol": symbol,
                        "base_date": target_date.strftime("%Y-%m-%d"),
                        "raw_data": json.dumps(item),
                        "collected_at": timestamp_now.isoformat(),
                        "available_at": timestamp_now.isoformat(),
                    }
                    for item in news_data["items"]
                ]
                loader.upsert_records("raw_disclosures", news_records)

            if not kis_ohlcv:
                raw_data = krx_collector.fetch_daily_ohlcv(symbol, target_date)
                if raw_data:
                    norm_data = StockNormalizer.normalize_krx_daily(raw_data, available_at)
                    loader.upsert_records("normalized_stock_prices_daily", [norm_data])

            total_processed += 1
        except Exception as exc:
            logger.error(f"Failed to process {symbol} ({name}): {exc}", exc_info=True)
            continue

    logger.info(
        f"Final flush: price={len(price_buf)}, supply={len(supply_buf)}, ratio={len(ratio_buf)}, master={len(master_buf)}"
    )
    if price_buf:
        loader.upsert_records("normalized_stock_prices_daily", price_buf)
    if supply_buf:
        loader.upsert_records("normalized_stock_supply_daily", supply_buf)
    if ratio_buf:
        loader.upsert_records("normalized_stock_fundamentals_ratios", ratio_buf)
    if master_buf:
        loader.upsert_records("stocks_master", master_buf)

    today_str = target_date.strftime("%Y-%m-%d")
    watch_tables = [
        "normalized_stock_prices_daily",
        "normalized_stock_supply_daily",
        "normalized_macro_series",
        "feature_store_daily",
    ]
    for table_name in watch_tables:
        try:
            res = (
                loader.client.table(table_name)
                .select("base_date", count="exact")
                .eq("base_date", today_str)
                .limit(1)
                .execute()
            )
            count = res.count if getattr(res, "count", None) else len(res.data or [])
            if count == 0:
                logger.critical(f"CRITICAL: No data loaded for [{table_name}] on {today_str}")
        except Exception as exc:
            logger.warning(f"Watch check failed for {table_name}: {exc}")

    await KISBaseCollector.close_session()
    loader.insert_log("daily_stock_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", total_processed)
    logger.info("Pipeline Finished Successfully.")


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
