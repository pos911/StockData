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
from src.utils.symbols import canonical_symbol_key, normalize_symbol_value

logger = get_logger(__name__)

_NON_COMMON_NAME_PATTERNS = [
    r"ETF",
    r"ETN",
    r"SPAC",
    r"REIT",
    r"Preferred",
    r"??",
    r"?캛$",
    r"1??",
    r"2??",
    r"3??",
]

_ETF_NAME_PREFIXES = ("KODEX", "TIGER", "RISE", "ACE", "SOL", "HANARO", "KOSEF", "TIMEFOLIO")
_ETN_NAME_MARKERS = ("ETN",)
_STANDARD_MARKETS = {"KOSPI", "KOSDAQ", "ETF", "ETN"}


def _is_etf_like_name(name: str | None) -> bool:
    if not name:
        return False
    upper_name = str(name).strip().upper()
    if "ETN" in upper_name:
        return False
    if upper_name.startswith("PLUS "):
        return True
    return upper_name.startswith(_ETF_NAME_PREFIXES) or " ETF" in upper_name or upper_name == "ETF"

def _prefer_symbol(existing_symbol: str, new_symbol: str) -> str:
    normalized_existing = normalize_symbol_value(existing_symbol)
    normalized_new = normalize_symbol_value(new_symbol)
    if normalized_existing and len(normalized_existing) >= len(normalized_new):
        return normalized_existing
    return normalized_new or normalized_existing


def _prefer_market(existing_market: str | None, new_market: str | None) -> str | None:
    existing = _standardize_market(existing_market)
    new = _standardize_market(new_market)
    if existing in {"ETF", "ETN"}:
        return existing
    if new in {"ETF", "ETN"}:
        return new
    if not existing:
        return new
    if existing == "DYNAMIC" and new:
        return new
    return existing


def _infer_market_from_name(name: str) -> str | None:
    if not name:
        return None
    upper_name = name.upper()
    if any(marker in upper_name for marker in _ETN_NAME_MARKERS):
        return "ETN"
    if _is_etf_like_name(name):
        return "ETF"
    return None


def _standardize_market(market: str | None, name: str | None = None) -> str | None:
    inferred = _infer_market_from_name(name or "")
    if inferred:
        return inferred
    if not market:
        return None
    value = str(market).strip().upper()
    if value in _STANDARD_MARKETS:
        return value
    if value in {"J", "STOCK", "KS", "KSE"}:
        return "KOSPI"
    if value in {"Q", "KQ", "KOSDAQ GLOBAL"}:
        return "KOSDAQ"
    return None


def _infer_asset_type(name: str | None, market: str | None) -> str:
    standardized_market = _standardize_market(market, name)
    if standardized_market in {"ETF", "ETN"}:
        return standardized_market
    return "STOCK"


def _should_fetch_fundamentals(name: str) -> bool:
    if not name:
        return True
    return not any(re.search(pattern, name) for pattern in _NON_COMMON_NAME_PATTERNS)


def _to_ratio_record(record: dict) -> dict:
    allowed_keys = {"symbol", "base_date", "per", "pbr", "roe", "debt_ratio", "source", "available_at"}
    return {key: value for key, value in record.items() if key in allowed_keys}


_PRICE_VALUE_FIELDS = ("open_price", "high_price", "low_price", "close_price", "volume", "trading_value")


def _is_blank(value) -> bool:
    return value is None or value == ""


def _is_valid_price_row(row: dict | None) -> bool:
    if not row:
        return False
    return not _is_blank(row.get("close_price")) and not _is_blank(row.get("volume")) and not _is_blank(row.get("trading_value"))


def _is_snapshot_only_price_row(row: dict | None) -> bool:
    if not row:
        return False
    return all(_is_blank(row.get(field)) for field in _PRICE_VALUE_FIELDS)


def _to_snapshot_record(snapshot: dict, available_at: str) -> dict:
    return {
        "symbol": snapshot.get("symbol"),
        "base_date": snapshot.get("base_date"),
        "market_cap": snapshot.get("market_cap"),
        "outstanding_shares": snapshot.get("listed_shares"),
        "foreign_holding_ratio": snapshot.get("foreign_holding_ratio"),
        "per": snapshot.get("per"),
        "pbr": snapshot.get("pbr"),
        "w52_high": snapshot.get("w52_high"),
        "w52_low": snapshot.get("w52_low"),
        "source": snapshot.get("source", "KIS"),
        "available_at": available_at,
    }


def _price_snapshot_update(snapshot: dict, available_at: str) -> dict:
    update_fields = {"available_at": available_at}
    if snapshot.get("market_cap") is not None:
        update_fields["market_cap"] = snapshot.get("market_cap")
    if snapshot.get("listed_shares") not in (None, 0):
        update_fields["outstanding_shares"] = snapshot.get("listed_shares")
    return update_fields


def _merge_latest_price_record(price_records: list, snapshot: dict) -> dict | None:
    if not price_records:
        return None
    target = next((row for row in price_records if row.get("base_date") == snapshot.get("base_date")), price_records[0])
    if not _is_valid_price_row(target):
        return None
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
        .select("symbol, base_date, open_price, high_price, low_price, close_price, volume, trading_value, market_cap, outstanding_shares")
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

        loader.upsert_records("normalized_stock_snapshots_daily", [_to_snapshot_record(snapshot, available_at)])

        price_row = prices_by_symbol.get(symbol)
        if not _is_valid_price_row(price_row):
            logger.warning(
                "skip_snapshot_only_price_repair: "
                f"symbol={symbol}, base_date={base_date_str}, "
                f"existing_price_row={bool(price_row)}, "
                f"close_price={price_row.get('close_price') if price_row else None}, "
                f"volume={price_row.get('volume') if price_row else None}, "
                f"trading_value={price_row.get('trading_value') if price_row else None}"
            )
        else:
            update_fields = _price_snapshot_update(snapshot, available_at)
            loader.update_record(
                "normalized_stock_prices_daily",
                {"symbol": symbol, "base_date": base_date_str},
                update_fields,
            )
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
        market = _standardize_market(item.get("market"), item.get("name")) or "KOSPI"
        asset_type = _infer_asset_type(item.get("name"), market)
        static_record = {
            "symbol": normalize_symbol_value(item.get("symbol")),
            "name": item.get("name"),
            "market": market,
            "asset_type": asset_type,
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
                    "asset_type": static_record["asset_type"],
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


def _sync_universe_to_master(
    loader: SupabaseLoader,
    universe: list[dict],
    activate_new: bool = False,
) -> int:
    if not universe:
        return 0

    now_iso = get_current_kst().isoformat()
    active_symbols = set()
    if not activate_new:
        try:
            res = loader.client.table("stocks_master").select("symbol").eq("is_active", True).execute()
            active_symbols = {row["symbol"] for row in (res.data or [])}
        except Exception as exc:
            logger.warning(f"Failed to read active stocks before full universe sync: {exc}")

    records = []
    seen = set()
    for stock in universe:
        symbol = normalize_symbol_value(stock.get("symbol", ""))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        records.append(
            {
                "symbol": symbol,
                "name": stock.get("name") or symbol,
                "market": _standardize_market(stock.get("market"), stock.get("name")) or "KOSPI",
                "asset_type": _infer_asset_type(stock.get("name"), stock.get("market")),
                "is_active": True if activate_new else symbol in active_symbols,
                "updated_at": now_iso,
            }
        )

    if records:
        loader.upsert_records("stocks_master", records)
    return len(records)


async def _collect_full_universe_prices(
    loader: SupabaseLoader,
    kis_collector: KISDomesticStockCollector,
    krx_collector: KRXCollector,
    universe: list[dict],
    target_date: date,
    available_at: str,
    limit: int | None = None,
) -> int:
    if not universe:
        logger.error("Full universe is empty; skip full price ingestion.")
        return 0

    target_universe = universe[:limit] if limit else universe
    base_ymd = target_date.strftime("%Y%m%d")
    processed = 0

    logger.info(
        f"Starting full-universe price ingestion: symbols={len(target_universe)}, date={base_ymd}"
    )
    for idx, stock in enumerate(target_universe, 1):
        symbol = normalize_symbol_value(stock.get("symbol", ""))
        name = stock.get("name") or symbol
        if not symbol:
            continue

        try:
            records = await kis_collector.fetch_ohlcv(
                symbol,
                timeframe="D",
                start_date=base_ymd,
                end_date=base_ymd,
                available_at=available_at,
            )
            valid_records = [record for record in records if _is_valid_price_row(record)]
            if records and not valid_records:
                logger.warning(
                    f"KIS returned only invalid price rows for full-universe symbol {name} ({symbol}); "
                    "trying KRX fallback."
                )
            if not valid_records:
                fallback = krx_collector.fetch_daily_ohlcv(symbol, target_date)
                if fallback:
                    fallback["base_date"] = target_date.strftime("%Y-%m-%d")
                    normalized = StockNormalizer.normalize_krx_daily(fallback, target_date)
                    normalized["available_at"] = available_at
                    loader.upsert_records("normalized_stock_prices_daily", [normalized])
                    valid_records = [normalized] if _is_valid_price_row(normalized) else []

            if valid_records:
                processed += 1
            else:
                logger.warning(f"No price row collected for full-universe symbol {name} ({symbol})")
        except Exception as exc:
            logger.error(f"Full-universe price ingestion failed for {name} ({symbol}): {exc}")

        if idx % 50 == 0:
            logger.info(f"Full-universe price ingestion progress: {idx}/{len(target_universe)}")
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(0.05)

    logger.info(f"Full-universe price ingestion finished: processed={processed}/{len(target_universe)}")
    return processed


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


def _fetch_price_quality(loader: SupabaseLoader, base_date_str: str) -> dict:
    rows = []
    market_by_symbol = {}
    try:
        rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", base_date_str, base_date_str)
    except Exception as exc:
        logger.warning(f"Failed to fetch price quality rows for {base_date_str}: {exc}")
    try:
        master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
        market_by_symbol = {row.get("symbol"): row.get("market") for row in master_rows if row.get("symbol")}
    except Exception as exc:
        logger.warning(f"Failed to fetch stocks_master for price market quality: {exc}")

    total = len(rows)
    null_close = sum(1 for row in rows if _is_blank(row.get("close_price")))
    null_volume = sum(1 for row in rows if _is_blank(row.get("volume")))
    null_trading = sum(1 for row in rows if _is_blank(row.get("trading_value")))
    snapshot_only = sum(1 for row in rows if _is_snapshot_only_price_row(row))
    valid = sum(1 for row in rows if _is_valid_price_row(row))
    market_not_null = sum(1 for row in rows if market_by_symbol.get(row.get("symbol")) in _STANDARD_MARKETS)
    return {
        "latest_price_date": base_date_str,
        "latest_total_rows": total,
        "null_close_rows": null_close,
        "null_volume_rows": null_volume,
        "null_trading_value_rows": null_trading,
        "snapshot_only_rows": snapshot_only,
        "valid_price_rows": valid,
        "market_not_null_rows": market_not_null,
    }


def _is_naver_news_ingestion_enabled(config: dict) -> bool:
    pipeline_config = config.get("pipeline", {})
    if "enable_naver_news_ingestion" in pipeline_config:
        return bool(pipeline_config.get("enable_naver_news_ingestion"))

    naver_config = config.get("naver", {})
    if "enabled" in naver_config:
        return bool(naver_config.get("enabled"))

    return False


def _infer_markets_from_supply_raw(loader: SupabaseLoader, base_date_str: str) -> dict:
    market_map = {}
    try:
        rows = loader.fetch_all("raw_stock_supply_daily", "base_date", base_date_str, base_date_str)
    except Exception as exc:
        logger.warning(f"Failed to load raw_stock_supply_daily for market inference: {exc}")
        return market_map

    for row in rows:
        symbol = row.get("symbol")
        raw_payload = row.get("raw_data")
        if not symbol or not raw_payload:
            continue
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except Exception:
            continue
        market_div_code = payload.get("market_div_code")
        if market_div_code == "J":
            market_map[symbol] = "KOSPI"
        elif market_div_code == "Q":
            market_map[symbol] = "KOSDAQ"
    return market_map


def _latest_valid_price_coverage(loader: SupabaseLoader) -> tuple[str | None, int]:
    try:
        latest_res = (
            loader.client.table("normalized_stock_prices_daily")
            .select("base_date")
            .order("base_date", desc=True)
            .limit(1)
            .execute()
        )
        if not latest_res.data:
            return None, 0
        latest_date = latest_res.data[0]["base_date"]
        rows = (
            loader.client.table("normalized_stock_prices_daily")
            .select("symbol, close_price, volume, trading_value")
            .eq("base_date", latest_date)
            .execute()
            .data
            or []
        )
        master_rows = (
            loader.client.table("stocks_master")
            .select("symbol, market, asset_type")
            .execute()
            .data
            or []
        )
        master_map = {row["symbol"]: row for row in master_rows if row.get("symbol")}
        count = 0
        for row in rows:
            master = master_map.get(row.get("symbol"))
            if not master:
                continue
            if master.get("market") not in {"KOSPI", "KOSDAQ"} or master.get("asset_type") != "STOCK":
                continue
            if row.get("close_price") in (None, "") or row.get("volume") in (None, "") or row.get("trading_value") in (None, ""):
                continue
            count += 1
        return latest_date, count
    except Exception as exc:
        logger.warning(f"Failed to compute latest valid price coverage: {exc}")
        return None, 0


def _should_skip_full_universe_price_ingestion(valid_stock_rows: int) -> bool:
    return valid_stock_rows >= 2000


def _count_active_master_symbols(loader: SupabaseLoader) -> int:
    try:
        res = loader.client.table("stocks_master").select("symbol", count="exact").eq("is_active", True).limit(1).execute()
        return int(res.count or 0)
    except Exception as exc:
        logger.warning(f"Failed to count active stocks_master rows: {exc}")
        return 0


def _detail_universe_source_counts(universe: list[dict]) -> dict[str, int]:
    counts = {"static": 0, "manual": 0, "ranking": 0}
    for stock in universe:
        source_text = str(stock.get("source_category") or "")
        parts = {part.strip() for part in source_text.split(",") if part.strip()}
        for key in counts:
            if key in parts:
                counts[key] += 1
    return counts


def _enforce_detail_universe_guardrail(
    universe: list[dict],
    limit: int | None,
    active_master_count: int,
    max_universe_size: int = 500,
) -> list[dict]:
    if limit is not None or len(universe) <= max_universe_size:
        return universe

    counts = _detail_universe_source_counts(universe)
    logger.error(
        "Detail universe exceeded guardrail; truncating to safe size. "
        f"static_count={counts['static']}, ranking_count={counts['ranking']}, "
        f"active_master_count={active_master_count}, final_universe_count={len(universe)}"
    )

    def _priority(stock: dict) -> tuple[int, str]:
        source_text = str(stock.get("source_category") or "")
        parts = {part.strip() for part in source_text.split(",") if part.strip()}
        if "static" in parts or "manual" in parts:
            return (0, stock.get("symbol", ""))
        if "ranking" in parts:
            return (1, stock.get("symbol", ""))
        return (2, stock.get("symbol", ""))

    return sorted(universe, key=_priority)[:max_universe_size]


async def run_pipeline(target_date: date, limit: int = None):
    logger.info(f"Starting Modernized Daily Stock Pipeline for {target_date}...")

    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()

    semaphore = asyncio.Semaphore(2)
    kis_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
    fundamentals_collector = KISFundamentalsCollector(config, auth_mgr, semaphore)

    universe_loader = DynamicUniverseLoader(config, kis_collector)
    krx_collector = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    available_at = generate_available_at_for_eod(target_date)
    base_date_str = target_date.strftime("%Y-%m-%d")
    latest_valid_price_date, latest_valid_price_count = _latest_valid_price_coverage(loader)
    full_price_processed = 0
    if _should_skip_full_universe_price_ingestion(latest_valid_price_count):
        logger.info(
            f"Skipping full-universe KIS OHLCV ingestion: latest_valid_price_date={latest_valid_price_date}, "
            f"valid_stock_rows={latest_valid_price_count}"
        )
    else:
        full_universe = universe_loader.fetch_full_universe(target_date=target_date)
        full_price_processed = await _collect_full_universe_prices(
            loader=loader,
            kis_collector=kis_collector,
            krx_collector=krx_collector,
            universe=full_universe,
            target_date=target_date,
            available_at=available_at.isoformat(),
            limit=limit,
        )

    universe = await universe_loader.get_combined_universe(auto_backfill=limit is None)
    active_master_count = _count_active_master_symbols(loader)
    market_classification_map = krx_collector.fetch_market_classification_map()
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

    timestamp_now = get_current_kst()
    total_processed = 0
    quality = {
        "total_symbols": 0,
        "price_success": 0,
        "supply_success": 0,
        "short_success": 0,
        "short_empty": 0,
        "short_failed": 0,
        "fundamentals_success": 0,
        "api_errors": 0,
        "db_errors": 0,
    }

    canonical_map = {}
    for stock in universe:
        symbol = stock["symbol"]
        key = canonical_symbol_key(symbol)
        if key not in canonical_map:
            canonical_map[key] = dict(stock)
        else:
            canonical_map[key]["symbol"] = _prefer_symbol(canonical_map[key].get("symbol"), symbol)
            canonical_map[key]["market"] = _prefer_market(canonical_map[key].get("market"), stock.get("market"))
            canonical_map[key]["asset_type"] = _infer_asset_type(
                canonical_map[key].get("name") or stock.get("name"),
                canonical_map[key].get("market"),
            )
    universe = list(canonical_map.values())

    for stock in universe:
        stock["symbol"] = normalize_symbol_value(stock["symbol"])
        listing_info = market_classification_map.get(canonical_symbol_key(stock["symbol"])) or market_classification_map.get(stock["symbol"])
        if listing_info:
            stock["name"] = listing_info.get("name") or stock["name"]
            stock["market"] = _prefer_market(stock.get("market"), listing_info.get("market"))
        stock["market"] = _standardize_market(_prefer_market(stock.get("market"), _infer_market_from_name(stock.get("name"))), stock.get("name")) or "KOSPI"
        stock["asset_type"] = _infer_asset_type(stock.get("name"), stock.get("market"))

    inferred_market_map = _infer_markets_from_supply_raw(loader, base_date_str)
    for stock in universe:
        inferred_market = inferred_market_map.get(stock["symbol"])
        if inferred_market:
            stock["market"] = _standardize_market(_prefer_market(stock.get("market"), inferred_market), stock.get("name")) or stock.get("market")
            stock["asset_type"] = _infer_asset_type(stock.get("name"), stock.get("market"))

    universe = _enforce_detail_universe_guardrail(universe, limit, active_master_count)

    if limit:
        logger.info(f"Limiting execution to first {limit} symbols for verification.")
        universe = universe[:limit]

    source_counts = _detail_universe_source_counts(universe)
    logger.info(
        f"Universe after dedup: {len(universe)} symbols "
        f"(static={source_counts['static']}, manual={source_counts['manual']}, "
        f"ranking={source_counts['ranking']}, active_master_count={active_master_count})"
    )
    quality["total_symbols"] = len(universe)

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
                market = _standardize_market(stock.get("market"), name) or "KOSPI"
                master_buf.append(
                    StockNormalizer.normalize_stock_master(
                        symbol,
                        name,
                        market,
                        asset_type=_infer_asset_type(name, market),
                    )
                )
                seen_master.add(symbol)
                flush_buf(master_buf, "stocks_master")

            if kis_ohlcv:
                quality["price_success"] += 1
            if supply_records:
                quality["supply_success"] += 1

            try:
                short_records = await kis_collector.fetch_short_selling(
                    symbol,
                    target_date=target_date,
                    market_code=stock.get("market"),
                    available_at=available_at.isoformat(),
                )
                if short_records:
                    quality["short_success"] += 1
                else:
                    quality["short_empty"] += 1
            except Exception as short_exc:
                quality["short_failed"] += 1
                logger.error(f"Short selling collection failed for {symbol}: {short_exc}", exc_info=True)
            base_date_str = target_date.strftime("%Y-%m-%d")
            snapshot_info = await kis_collector.fetch_fundamental_info(
                symbol,
                base_date=base_date_str,
                available_at=available_at.isoformat(),
            )
            if snapshot_info:
                loader.upsert_records(
                    "normalized_stock_snapshots_daily",
                    [_to_snapshot_record(snapshot_info, available_at.isoformat())],
                )
                enriched_price = _merge_latest_price_record(kis_ohlcv, snapshot_info)
                if enriched_price:
                    loader.upsert_records("normalized_stock_prices_daily", [enriched_price])
                else:
                    try:
                        existing_price = (
                            loader.client.table("normalized_stock_prices_daily")
                            .select("symbol, base_date, open_price, high_price, low_price, close_price, volume, trading_value")
                            .eq("symbol", symbol)
                            .eq("base_date", base_date_str)
                            .limit(1)
                            .execute()
                        )
                        existing_row = (existing_price.data or [None])[0]
                        if _is_valid_price_row(existing_row):
                            loader.update_record(
                                "normalized_stock_prices_daily",
                                {"symbol": symbol, "base_date": base_date_str},
                                _price_snapshot_update(snapshot_info, available_at.isoformat()),
                            )
                    except Exception as update_exc:
                        logger.warning(f"Snapshot price-field update skipped for {symbol}: {update_exc}")

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
                quality["fundamentals_success"] += 1
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
            quality["api_errors"] += 1
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
    price_rate = quality["price_success"] / max(quality["total_symbols"], 1)
    supply_rate = quality["supply_success"] / max(quality["total_symbols"], 1)
    price_quality = _fetch_price_quality(loader, target_date.strftime("%Y-%m-%d"))
    quality["valid_price_rows"] = price_quality["valid_price_rows"]
    quality["snapshot_only_price_rows"] = price_quality["snapshot_only_rows"]
    quality["price_null_volume_rows"] = price_quality["null_volume_rows"]
    quality["price_null_trading_value_rows"] = price_quality["null_trading_value_rows"]
    issues = []
    status = "SUCCESS"
    if price_quality["snapshot_only_rows"] > 0:
        status = "FAIL"
        issues.append(f"snapshot_only_price_rows={price_quality['snapshot_only_rows']}")
    if price_quality["valid_price_rows"] == 0:
        status = "FAIL"
        issues.append("valid_price_rows=0")
    if price_rate < 0.8:
        status = "FAIL"
        issues.append(f"price coverage low: {quality['price_success']}/{quality['total_symbols']}")
    elif supply_rate < 0.5:
        status = "WARN"
        issues.append(f"supply coverage low: {quality['supply_success']}/{quality['total_symbols']}")
    if quality["short_failed"] > 0:
        status = "FAIL" if status == "FAIL" else "WARN"
        issues.append(f"short_failed={quality['short_failed']}")
    if quality["api_errors"] > 0:
        status = "FAIL" if status == "FAIL" else "WARN"
        issues.append(f"api_errors={quality['api_errors']}")

    logger.info("=== DATA QUALITY SUMMARY ===")
    for key, value in quality.items():
        logger.info(f"{key}: {value}")
    for key, value in price_quality.items():
        logger.info(f"price_quality.{key}: {value}")
    logger.info(f"status: {status}")
    if issues:
        logger.warning("quality_issues: " + "; ".join(issues))

    loader.insert_log(
        "daily_stock_pipeline",
        target_date.strftime("%Y-%m-%d"),
        status,
        total_processed,
        "; ".join(issues),
    )
    loader.insert_log(
        "daily_stock_full_price_pipeline",
        target_date.strftime("%Y-%m-%d"),
        "SUCCESS" if full_price_processed > 0 or _should_skip_full_universe_price_ingestion(latest_valid_price_count) else "WARN",
        full_price_processed,
        "" if full_price_processed > 0 else (
            f"skipped_full_universe_prices latest_valid_price_date={latest_valid_price_date} "
            f"valid_stock_rows={latest_valid_price_count}"
            if _should_skip_full_universe_price_ingestion(latest_valid_price_count)
            else ""
        ),
    )
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
