import asyncio
import json
import argparse
import re
import os
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


def _to_ratio_record(record: dict) -> dict:
    allowed_keys = {"symbol", "base_date", "per", "pbr", "roe", "debt_ratio", "source", "available_at"}
    return {key: value for key, value in record.items() if key in allowed_keys}


def _merge_latest_price_record(price_records: list, snapshot: dict) -> dict | None:
    if not price_records:
        return None
    target = next((row for row in price_records if row.get("base_date") == snapshot.get("base_date")), price_records[0])
    merged = dict(target)
    merged["market_cap"] = snapshot.get("market_cap")
    merged["outstanding_shares"] = snapshot.get("listed_shares")
    return merged


def _merge_latest_supply_record(supply_records: list, snapshot: dict) -> dict | None:
    if not supply_records:
        return None
    target = next((row for row in supply_records if row.get("base_date") == snapshot.get("base_date")), supply_records[0])
    merged = dict(target)
    merged["foreign_holding_ratio"] = snapshot.get("foreign_holding_ratio")
    return merged


async def _repair_missing_snapshot_fields(
    loader: SupabaseLoader,
    collector: KISDomesticStockCollector,
    target_date: date,
    available_at: str,
):
    base_date_str = target_date.strftime("%Y-%m-%d")

    active_res = loader.client.table("stocks_master").select("symbol").eq("is_active", True).execute()
    active_symbols = {row["symbol"] for row in (active_res.data or [])}
    if not active_symbols:
        return

    price_res = (
        loader.client.table("normalized_stock_prices_daily")
        .select("symbol, base_date, market_cap, outstanding_shares")
        .eq("base_date", base_date_str)
        .execute()
    )
    supply_res = (
        loader.client.table("normalized_stock_supply_daily")
        .select("symbol, base_date, foreign_holding_ratio")
        .eq("base_date", base_date_str)
        .execute()
    )

    prices_by_symbol = {row["symbol"]: row for row in (price_res.data or []) if row.get("symbol") in active_symbols}
    supply_by_symbol = {row["symbol"]: row for row in (supply_res.data or []) if row.get("symbol") in active_symbols}

    missing_symbols = set()
    for symbol in active_symbols:
        price_row = prices_by_symbol.get(symbol)
        supply_row = supply_by_symbol.get(symbol)
        if not price_row or price_row.get("market_cap") is None or price_row.get("outstanding_shares") is None:
            missing_symbols.add(symbol)
        if not supply_row or supply_row.get("foreign_holding_ratio") is None:
            missing_symbols.add(symbol)

    if not missing_symbols:
        logger.info(f"No missing snapshot fields detected for {base_date_str}.")
        return

    logger.info(f"Repairing snapshot fields for {len(missing_symbols)} symbols on {base_date_str}.")
    repaired_prices = 0
    repaired_supply = 0

    for symbol in sorted(missing_symbols):
        snapshot = await collector.fetch_fundamental_info(
            symbol,
            base_date=base_date_str,
            available_at=available_at,
        )
        if not snapshot:
            logger.warning(f"[Repair] No fundamental snapshot returned for {symbol}")
            continue

        price_payload = {
            "symbol": symbol,
            "base_date": base_date_str,
            "market_cap": snapshot.get("market_cap"),
            "outstanding_shares": snapshot.get("listed_shares"),
            "available_at": available_at,
        }
        if price_payload["market_cap"] is not None or price_payload["outstanding_shares"] not in (None, 0):
            loader.upsert_records("normalized_stock_prices_daily", [price_payload])
            repaired_prices += 1

        supply_payload = {
            "symbol": symbol,
            "base_date": base_date_str,
            "foreign_holding_ratio": snapshot.get("foreign_holding_ratio"),
            "available_at": available_at,
        }
        if supply_payload["foreign_holding_ratio"] not in (None, 0):
            loader.upsert_records("normalized_stock_supply_daily", [supply_payload])
            repaired_supply += 1

    logger.info(
        f"Snapshot repair finished for {base_date_str}: "
        f"price_rows={repaired_prices}, supply_rows={repaired_supply}"
    )


def _sync_static_universe_to_master(loader: SupabaseLoader):
    path = "config/stock_universe.json"
    if not os.path.exists(path):
        logger.warning("Static universe file is missing; skip stocks_master sync.")
        return 0

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    synced_at = get_current_kst().isoformat()
    static_records = []
    enabled_records = []
    for item in data:
        static_record = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "market": item.get("market"),
            "enabled": bool(item.get("enabled", True)),
            "source_file": path,
            "updated_at": synced_at,
        }
        static_records.append(static_record)
        if static_record["enabled"]:
            enabled_records.append(
                {
                    "symbol": static_record["symbol"],
                    "name": static_record["name"],
                    "market": static_record["market"],
                    "is_active": True,
                    "updated_at": synced_at,
                }
            )

    try:
        existing_table_res = loader.client.table("static_stock_universe").select("symbol").execute()
        existing_static_symbols = {row["symbol"] for row in (existing_table_res.data or [])}
    except Exception as exc:
        logger.error(f"Failed to fetch static_stock_universe: {exc}")
        existing_static_symbols = set()

    file_symbols = {record["symbol"] for record in static_records if record.get("symbol")}
    removed_symbols = sorted(existing_static_symbols - file_symbols)

    if static_records:
        loader.upsert_records("static_stock_universe", static_records)

    for symbol in removed_symbols:
        loader.delete_records("static_stock_universe", eq_filters={"symbol": symbol})
        loader.delete_records("stocks_master", eq_filters={"symbol": symbol})

    disabled_symbols = sorted(
        record["symbol"]
        for record in static_records
        if record.get("symbol") and not record.get("enabled", True)
    )
    for symbol in disabled_symbols:
        loader.delete_records("stocks_master", eq_filters={"symbol": symbol})

    if enabled_records:
        loader.upsert_records("stocks_master", enabled_records)

    logger.info(
        f"Synchronized static universe: total={len(static_records)}, "
        f"enabled={len(enabled_records)}, removed={len(removed_symbols)}, disabled={len(disabled_symbols)}"
    )
    return len(enabled_records)


def _refresh_recent_naver_news(
    loader: SupabaseLoader,
    symbol: str,
    news_items: list,
    timestamp_now,
):
    loader.delete_records(
        "raw_disclosures",
        eq_filters={
            "source": "NaverNews",
            "symbol": symbol,
        },
    )

    if not news_items:
        logger.info(f"No recent Naver news within 12 hours for {symbol}.")
        return 0

    news_records = [
        {
            "source": "NaverNews",
            "symbol": symbol,
            "base_date": timestamp_now.strftime("%Y-%m-%d"),
            "raw_data": json.dumps(item, ensure_ascii=False),
            "collected_at": timestamp_now.isoformat(),
            "available_at": timestamp_now.isoformat(),
        }
        for item in news_items
    ]
    loader.upsert_records("raw_disclosures", news_records)
    return len(news_records)


def _is_naver_news_ingestion_enabled(config: dict) -> bool:
    pipeline_config = config.get("pipeline", {})
    if "enable_naver_news_ingestion" in pipeline_config:
        return bool(pipeline_config.get("enable_naver_news_ingestion"))

    naver_config = config.get("naver", {})
    if "enabled" in naver_config:
        return bool(naver_config.get("enabled"))

    return False


async def run_pipeline(target_date: date, limit: int = None):
    logger.info(f"Starting Modernized Daily Stock Pipeline for {target_date}...")

    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    _sync_static_universe_to_master(loader)

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()

    semaphore = asyncio.Semaphore(2)
    kis_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
    fundamentals_collector = KISFundamentalsCollector(config, auth_mgr, semaphore)

    universe_loader = DynamicUniverseLoader(config, kis_collector)
    universe = await universe_loader.get_combined_universe()

    krx_collector = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    opendart_collector = OpenDartCollector(api_key=config.get("opendart", {}).get("api_key", ""))
    naver_news_enabled = _is_naver_news_ingestion_enabled(config)
    naver_collector = None
    if naver_news_enabled:
        naver_collector = NaverNewsCollector(
            client_id=config.get("naver", {}).get("client_id", ""),
            client_secret=config.get("naver", {}).get("client_secret", ""),
        )
    else:
        logger.info("Naver news ingestion is disabled by configuration.")

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
            supply_records = await kis_collector.fetch_investor_trend(symbol, available_at=available_at.isoformat())
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

                    bf_snapshot = await kis_collector.fetch_fundamental_info(
                        symbol,
                        base_date=target_date.strftime("%Y-%m-%d"),
                        available_at=available_at.isoformat(),
                    )
                    if bf_snapshot:
                        ratio_buf.append(_to_ratio_record(bf_snapshot))
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
            base_date_str = target_date.strftime("%Y-%m-%d")
            snapshot_info = await kis_collector.fetch_fundamental_info(
                symbol,
                base_date=base_date_str,
                available_at=available_at.isoformat(),
            )
            if snapshot_info:
                enriched_price = _merge_latest_price_record(kis_ohlcv, snapshot_info)
                if enriched_price:
                    loader.upsert_records("normalized_stock_prices_daily", [enriched_price])

                enriched_supply = _merge_latest_supply_record(supply_records, snapshot_info)
                if enriched_supply:
                    loader.upsert_records("normalized_stock_supply_daily", [enriched_supply])

            if _should_fetch_fundamentals(name):
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

            if naver_collector is not None:
                news_data = naver_collector.fetch_news(
                    f"{name} {symbol}",
                    freshness_hours=12,
                    now=timestamp_now,
                )
                _refresh_recent_naver_news(
                    loader=loader,
                    symbol=symbol,
                    news_items=(news_data or {}).get("items", []),
                    timestamp_now=timestamp_now,
                )

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

    await _repair_missing_snapshot_fields(
        loader=loader,
        collector=kis_collector,
        target_date=target_date,
        available_at=available_at.isoformat(),
    )

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

    await auth_mgr.shutdown()
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
