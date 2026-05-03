from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from typing import Any

from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import generate_available_at_for_eod, get_current_kst, parse_date_string

logger = get_logger(__name__)


def _parse_numeric(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _standardize_market(market: str | None) -> str | None:
    if not market:
        return None
    value = str(market).strip().upper()
    if value in {"KOSPI", "KOSDAQ", "ETF", "ETN", "KOSPI200"}:
        return value
    if value in {"J", "STOCK", "KS", "KSE"}:
        return "KOSPI"
    if value in {"Q", "KQ"}:
        return "KOSDAQ"
    if value == "T":
        return "ETF"
    return None


def _load_master_map(loader: SupabaseLoader) -> dict[str, dict[str, Any]]:
    rows = loader.client.table("stocks_master").select("symbol, name, market, asset_type, is_active").execute().data or []
    mapping = {}
    for row in rows:
        symbol = normalize_symbol_value(row.get("symbol"))
        if not symbol:
            continue
        copied = dict(row)
        copied["symbol"] = symbol
        copied["market"] = _standardize_market(copied.get("market"))
        mapping[symbol] = copied
    return mapping


def _delete_existing_rankings(loader: SupabaseLoader, base_date: str, market: str, rank_type: str, source: str) -> None:
    for table_name in ("normalized_market_rankings_daily", "raw_market_rankings"):
        query = loader.client.table(table_name).delete().eq("base_date", base_date).eq("market", market).eq("rank_type", rank_type).eq("source", source)
        query.execute()


def _persist_rankings(
    loader: SupabaseLoader,
    target_date: date,
    market: str,
    rank_type: str,
    source: str,
    rows: list[dict[str, Any]],
) -> int:
    base_date = target_date.strftime("%Y-%m-%d")
    available_at = generate_available_at_for_eod(target_date).isoformat()
    _delete_existing_rankings(loader, base_date, market, rank_type, source)
    raw_records = []
    normalized_records = []
    for rank, row in enumerate(rows, 1):
        raw_records.append(
            {
                "source": source,
                "base_date": base_date,
                "market": market,
                "rank_type": rank_type,
                "symbol": row["symbol"],
                "name": row["name"],
                "raw_rank": rank,
                "raw_data": json.dumps(row["raw_data"], ensure_ascii=False),
                "available_at": available_at,
            }
        )
        normalized_records.append(
            {
                "base_date": base_date,
                "market": market,
                "rank_type": rank_type,
                "rank": rank,
                "symbol": row["symbol"],
                "name": row["name"],
                "volume": row.get("volume"),
                "trading_value": row.get("trading_value"),
                "market_cap": row.get("market_cap"),
                "change_rate": row.get("change_rate"),
                "metric_value": row.get("metric_value"),
                "source": source,
                "available_at": available_at,
            }
        )
    if raw_records:
        loader.upsert_records("raw_market_rankings", raw_records)
    if normalized_records:
        loader.upsert_records("normalized_market_rankings_daily", normalized_records)
    return len(normalized_records)


def _filter_kis_volume_rows(
    rows: list[dict[str, Any]],
    master_map: dict[str, dict[str, Any]],
    target_market: str,
) -> list[dict[str, Any]]:
    filtered = []
    dropped = 0
    for row in rows or []:
        symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
        master = master_map.get(symbol)
        if not master:
            dropped += 1
            continue
        if master.get("market") != target_market or master.get("asset_type") != "STOCK":
            dropped += 1
            continue
        filtered.append(
            {
                "symbol": symbol,
                "name": master.get("name") or row.get("hts_kor_isnm") or symbol,
                "volume": _parse_numeric(row.get("acml_vol")),
                "trading_value": _parse_numeric(row.get("acml_tr_pbmn")),
                "market_cap": None,
                "change_rate": _parse_numeric(row.get("prdy_ctrt")),
                "metric_value": _parse_numeric(row.get("acml_vol")),
                "raw_data": {
                    "response_row": row,
                    "master_market": master.get("market"),
                    "master_asset_type": master.get("asset_type"),
                },
            }
        )
    if dropped:
        logger.warning(f"Dropped {dropped} KIS volume-rank rows for target_market={target_market} after master validation.")
    filtered.sort(key=lambda item: (item.get("metric_value") or 0), reverse=True)
    return filtered


def _build_price_based_rankings(
    loader: SupabaseLoader,
    target_date: date,
    master_map: dict[str, dict[str, Any]],
    market: str,
    rank_type: str,
    limit: int,
) -> tuple[str, list[dict[str, Any]]]:
    latest_res = (
        loader.client.table("normalized_stock_prices_daily")
        .select("base_date")
        .lte("base_date", target_date.strftime("%Y-%m-%d"))
        .order("base_date", desc=True)
        .limit(1)
        .execute()
    )
    if not latest_res.data:
        return "VALID_PRICE_FALLBACK", []
    price_base_date = latest_res.data[0]["base_date"]
    rows = (
        loader.client.table("normalized_stock_prices_daily")
        .select("symbol, close_price, volume, trading_value, market_cap")
        .eq("base_date", price_base_date)
        .execute()
        .data
        or []
    )
    metric_key = rank_type
    source = "KRX" if market in {"ETF", "ETN"} and price_base_date == target_date.strftime("%Y-%m-%d") else "VALID_PRICE_FALLBACK"
    filtered = []
    for row in rows:
        symbol = normalize_symbol_value(row.get("symbol"))
        master = master_map.get(symbol)
        if not master:
            continue
        if master.get("market") != market:
            continue
        metric_value = row.get(metric_key)
        if metric_value in (None, ""):
            continue
        filtered.append(
            {
                "symbol": symbol,
                "name": master.get("name") or symbol,
                "volume": row.get("volume"),
                "trading_value": row.get("trading_value"),
                "market_cap": row.get("market_cap"),
                "change_rate": None,
                "metric_value": metric_value,
                "raw_data": {
                    "price_base_date": price_base_date,
                    "master_market": master.get("market"),
                    "master_asset_type": master.get("asset_type"),
                },
            }
        )
    filtered.sort(key=lambda item: (item.get("metric_value") or 0), reverse=True)
    return source, filtered[:limit]


async def run_pipeline(target_date: date) -> None:
    logger.info(f"Starting daily ranking pipeline for {target_date:%Y-%m-%d}")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    master_map = _load_master_map(loader)

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    semaphore = asyncio.Semaphore(2)
    collector = KISDomesticStockCollector(config, auth_mgr, semaphore)

    try:
        kospi_rows = await collector.fetch_volume_rank(market_code="K")
        kospi_filtered = _filter_kis_volume_rows(kospi_rows, master_map, "KOSPI")
        if len(kospi_filtered) < 20:
            logger.warning(f"KIS market_code=K returned only {len(kospi_filtered)} valid KOSPI rows; trying J fallback.")
            kospi_filtered = _filter_kis_volume_rows(await collector.fetch_volume_rank(market_code="J"), master_map, "KOSPI")

        kosdaq_filtered = _filter_kis_volume_rows(await collector.fetch_volume_rank(market_code="Q"), master_map, "KOSDAQ")

        kospi_count = _persist_rankings(loader, target_date, "KOSPI", "volume", "KIS", kospi_filtered[:30])
        kosdaq_count = _persist_rankings(loader, target_date, "KOSDAQ", "volume", "KIS", kosdaq_filtered[:30])

        if kospi_count < 20:
            logger.warning(f"KOSPI volume ranking has fewer than 20 validated rows: {kospi_count}")
        if kosdaq_count < 20:
            logger.warning(f"KOSDAQ volume ranking has fewer than 20 validated rows: {kosdaq_count}")

        for market, limit in (("KOSPI", 30), ("KOSDAQ", 30), ("ETF", 20), ("ETN", 20)):
            source, rows = _build_price_based_rankings(loader, target_date, master_map, market, "trading_value", limit)
            _persist_rankings(loader, target_date, market, "trading_value", source, rows)

        for market, limit in (("KOSPI", 30), ("KOSDAQ", 30), ("ETF", 20), ("ETN", 20)):
            source, rows = _build_price_based_rankings(loader, target_date, master_map, market, "market_cap", limit)
            _persist_rankings(loader, target_date, market, "market_cap", source, rows)

        loader.insert_log(
            "daily_ranking_pipeline",
            target_date.strftime("%Y-%m-%d"),
            "SUCCESS",
            kospi_count + kosdaq_count,
        )
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    args = parser.parse_args()
    target_dt = get_current_kst().date()
    if args.date:
        target_dt = parse_date_string(args.date)
    asyncio.run(run_pipeline(target_dt))
