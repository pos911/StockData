from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.market_data_quality import get_latest_valid_price_date
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import generate_available_at_for_eod, get_current_kst, parse_date_string

logger = get_logger(__name__)

VOLUME_THRESHOLDS = {
    "KOSPI": 20,
    "KOSDAQ": 20,
    "ETF": 10,
    "ETN": 5,
}

RANKING_LIMITS = {
    "KOSPI": 30,
    "KOSDAQ": 30,
    "ETF": 20,
    "ETN": 20,
}


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


def _delete_rankings(loader: SupabaseLoader, base_date: str, market: str, rank_type: str, sources: list[str] | None = None) -> None:
    target_sources = sources or ["KIS", "VALID_PRICE_FALLBACK", "KRX"]
    for source in target_sources:
        for table_name in ("normalized_market_rankings_daily", "raw_market_rankings"):
            query = (
                loader.client.table(table_name)
                .delete()
                .eq("base_date", base_date)
                .eq("market", market)
                .eq("rank_type", rank_type)
                .eq("source", source)
            )
            query.execute()


def _persist_rankings(
    loader: SupabaseLoader,
    target_date: date,
    market: str,
    rank_type: str,
    source: str,
    rows: list[dict[str, Any]],
    replace_existing: bool = True,
) -> int:
    base_date = target_date.strftime("%Y-%m-%d")
    available_at = generate_available_at_for_eod(target_date).isoformat()
    if replace_existing:
        _delete_rankings(loader, base_date, market, rank_type)

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


def _classify_kis_volume_rows(rows: list[dict[str, Any]], master_map: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
        if not symbol:
            continue
        master = master_map.get(symbol)
        if not master:
            continue
        market = master.get("market")
        asset_type = master.get("asset_type")
        if market == "KOSPI" and asset_type == "STOCK":
            bucket = "KOSPI"
        elif market == "KOSDAQ" and asset_type == "STOCK":
            bucket = "KOSDAQ"
        elif market == "ETF" and asset_type == "ETF":
            bucket = "ETF"
        elif market == "ETN" and asset_type == "ETN":
            bucket = "ETN"
        else:
            continue
        buckets[bucket].append(
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
                    "master_market": market,
                    "master_asset_type": asset_type,
                },
            }
        )
    for market, bucket_rows in buckets.items():
        bucket_rows.sort(
            key=lambda item: ((item.get("volume") or 0), (item.get("trading_value") or 0)),
            reverse=True,
        )
        buckets[market] = bucket_rows
    return buckets


def _filter_kis_volume_rows(
    rows: list[dict[str, Any]],
    master_map: dict[str, dict[str, Any]],
    target_market: str,
) -> list[dict[str, Any]]:
    return _classify_kis_volume_rows(rows, master_map).get(target_market, [])


def _build_price_based_rankings(
    loader: SupabaseLoader,
    target_date: date,
    master_map: dict[str, dict[str, Any]],
    market: str,
    rank_type: str,
    limit: int,
    fallback_reason: str | None = None,
    original_kis_count: int | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    quality = get_latest_valid_price_date(loader, target_date, lookback_days=10, min_valid_rows=100)
    price_base_date = quality.get("selected_price_base_date")

    def _load_rows(base_date: str):
        return loader.fetch_all("normalized_stock_prices_daily", "base_date", base_date, base_date)

    def _filter_rows(rows: list[dict[str, Any]], row_base_date: str):
        metric_key = rank_type
        filtered_local = []
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
            if rank_type == "volume" and row.get("volume") in (None, ""):
                continue
            if rank_type == "trading_value" and row.get("trading_value") in (None, ""):
                continue
            if rank_type == "market_cap" and row.get("market_cap") in (None, ""):
                continue
            filtered_local.append(
                {
                    "symbol": symbol,
                    "name": master.get("name") or symbol,
                    "volume": row.get("volume"),
                    "trading_value": row.get("trading_value"),
                    "market_cap": row.get("market_cap"),
                    "change_rate": None,
                    "metric_value": metric_value,
                    "raw_data": {
                        "price_base_date": row_base_date,
                        "ranking_base_date": target_date.strftime("%Y-%m-%d"),
                        "fallback_reason": fallback_reason,
                        "original_kis_count": original_kis_count,
                        "master_market": master.get("market"),
                        "master_asset_type": master.get("asset_type"),
                    },
                }
            )
        return filtered_local
    candidate_dates = []
    if price_base_date:
        candidate_dates.append(price_base_date)
    additional_dates = sorted(
        {
            str(row.get("base_date"))
            for row in loader.fetch_all(
                "normalized_stock_prices_daily",
                "base_date",
                (target_date - timedelta(days=10)).strftime("%Y-%m-%d"),
                target_date.strftime("%Y-%m-%d"),
            )
            if row.get("base_date")
        },
        reverse=True,
    )
    candidate_dates.extend([candidate for candidate in additional_dates if candidate not in candidate_dates])

    filtered = []
    selected_from_market_specific = False
    for candidate_date in candidate_dates:
        candidate_rows = _load_rows(candidate_date)
        candidate_filtered = _filter_rows(candidate_rows, candidate_date)
        if candidate_filtered:
            selected_from_market_specific = candidate_date != price_base_date
            price_base_date = candidate_date
            filtered = candidate_filtered
            break
    if selected_from_market_specific:
        logger.warning(
            f"{market} {rank_type} using market-specific price fallback date {price_base_date} "
            f"instead of selected global date {quality.get('selected_price_base_date')}."
        )
    if not filtered:
        return "VALID_PRICE_FALLBACK", [], None
    source = "KRX" if market in {"ETF", "ETN"} and price_base_date == target_date.strftime("%Y-%m-%d") else "VALID_PRICE_FALLBACK"
    filtered.sort(
        key=lambda item: ((item.get("metric_value") or 0), (item.get("trading_value") or 0)),
        reverse=True,
    )
    if len(filtered) < limit:
        logger.warning(
            f"{market} {rank_type} ranking has only {len(filtered)} valid price candidates on price_base_date={price_base_date}"
        )
    return source, filtered[:limit], price_base_date


def _select_volume_rankings(
    loader: SupabaseLoader,
    target_date: date,
    market: str,
    kis_rows: list[dict[str, Any]],
    master_map: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    threshold = VOLUME_THRESHOLDS[market]
    limit = RANKING_LIMITS[market]
    if len(kis_rows) >= threshold:
        return "KIS", kis_rows[:limit]

    logger.warning(
        f"{market} KIS volume ranking is sparse: count={len(kis_rows)}, threshold={threshold}. "
        "Replacing with valid price fallback."
    )
    source, rows, _price_base_date = _build_price_based_rankings(
        loader=loader,
        target_date=target_date,
        master_map=master_map,
        market=market,
        rank_type="volume",
        limit=limit,
        fallback_reason="kis_volume_sparse",
        original_kis_count=len(kis_rows),
    )
    if rows:
        return source, rows
    if kis_rows:
        logger.warning(
            f"{market} valid price fallback returned 0 rows; retaining sparse KIS volume rows instead."
        )
        return "KIS", kis_rows[:limit]
    return source, rows


async def run_pipeline(target_date: date, dry_run: bool = False) -> None:
    logger.info(f"Starting daily ranking pipeline for {target_date:%Y-%m-%d}")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    master_map = _load_master_map(loader)

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    semaphore = asyncio.Semaphore(2)
    collector = KISDomesticStockCollector(config, auth_mgr, semaphore)

    try:
        kis_j_rows = await collector.fetch_volume_rank(market_code="J")
        classified = _classify_kis_volume_rows(kis_j_rows, master_map)
        ranking_total = 0

        for market in ("KOSPI", "KOSDAQ", "ETF", "ETN"):
            source, volume_rows = _select_volume_rankings(
                loader=loader,
                target_date=target_date,
                market=market,
                kis_rows=classified.get(market, []),
                master_map=master_map,
            )
            if not dry_run:
                ranking_total += _persist_rankings(loader, target_date, market, "volume", source, volume_rows)
            else:
                logger.info(f"[dry-run] {market} volume source={source} rows={len(volume_rows)}")

        for market, limit in RANKING_LIMITS.items():
            source, rows, price_base_date = _build_price_based_rankings(
                loader, target_date, master_map, market, "trading_value", limit
            )
            logger.info(f"{market} trading_value ranking price_base_date={price_base_date} source={source} rows={len(rows)}")
            if not dry_run:
                _persist_rankings(loader, target_date, market, "trading_value", source, rows)

        for market, limit in RANKING_LIMITS.items():
            source, rows, price_base_date = _build_price_based_rankings(
                loader, target_date, master_map, market, "market_cap", limit
            )
            logger.info(f"{market} market_cap ranking price_base_date={price_base_date} source={source} rows={len(rows)}")
            if not dry_run:
                _persist_rankings(loader, target_date, market, "market_cap", source, rows)

        if not dry_run:
            loader.insert_log(
                "daily_ranking_pipeline",
                target_date.strftime("%Y-%m-%d"),
                "SUCCESS",
                ranking_total,
            )
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target_dt = get_current_kst().date()
    if args.date:
        target_dt = parse_date_string(args.date)
    asyncio.run(run_pipeline(target_dt, dry_run=args.dry_run))
