from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.market_data_quality import get_latest_valid_price_date, is_valid_price_row
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import generate_available_at_for_eod, get_current_utc, get_kst_target_date, parse_date_string
from src.utils.trading_calendar import get_previous_trading_day, should_skip_market_job

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

KR_STOCK_MARKETS = {"KOSPI", "KOSDAQ"}
KR_STOCK_PRICE_READY_THRESHOLDS = {
    "KOSPI": 700,
    "KOSDAQ": 1200,
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
    source_base_date: str | None = None,
    replace_existing: bool = True,
) -> int:
    base_date = target_date.strftime("%Y-%m-%d")
    available_at = generate_available_at_for_eod(target_date).isoformat()
    if replace_existing:
        _delete_rankings(loader, base_date, market, rank_type)

    raw_records = []
    normalized_records = []
    supports_source_base_date = _supports_source_base_date(loader)
    for rank, row in enumerate(rows, 1):
        raw_data = dict(row["raw_data"])
        raw_data.setdefault("source_base_date", source_base_date)
        raw_data.setdefault("ranking_base_date", base_date)
        raw_records.append(
            {
                "source": source,
                "base_date": base_date,
                "market": market,
                "rank_type": rank_type,
                "symbol": row["symbol"],
                "name": row["name"],
                "raw_rank": rank,
                "raw_data": json.dumps(raw_data, ensure_ascii=False),
                "available_at": available_at,
            }
        )
        normalized_row = {
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
        if supports_source_base_date:
            normalized_row["source_base_date"] = source_base_date
        normalized_records.append(normalized_row)
    if raw_records:
        loader.upsert_records("raw_market_rankings", raw_records)
    if normalized_records:
        loader.upsert_records("normalized_market_rankings_daily", normalized_records)
    return len(normalized_records)


def _supports_source_base_date(loader: SupabaseLoader) -> bool:
    if getattr(loader, "_source_base_date_supported", None) is not None:
        return bool(loader._source_base_date_supported)
    try:
        loader.client.table("normalized_market_rankings_daily").select("source_base_date").limit(1).execute()
        loader._source_base_date_supported = True
    except Exception as exc:
        logger.warning(
            "normalized_market_rankings_daily.source_base_date is not available yet. "
            "Run sql/add_ranking_source_base_date.sql to persist ranking source dates. "
            f"detail={exc}"
        )
        loader._source_base_date_supported = False
    return bool(loader._source_base_date_supported)


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
            if not is_valid_price_row(row, market_is_open=True):
                continue
            metric_value = row.get(metric_key)
            if metric_value in (None, ""):
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
                        "source_base_date": row_base_date,
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


def _count_valid_kr_stock_rows(loader: SupabaseLoader, target_date: date, master_map: dict[str, dict[str, Any]]) -> tuple[int, dict[str, int]]:
    target_date_str = target_date.strftime("%Y-%m-%d")
    rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", target_date_str, target_date_str)
    market_counts = defaultdict(int)
    total = 0
    for row in rows:
        if not is_valid_price_row(row, market_is_open=True):
            continue
        master = master_map.get(normalize_symbol_value(row.get("symbol")))
        if not master:
            continue
        market = master.get("market")
        asset_type = master.get("asset_type")
        if market in KR_STOCK_MARKETS and asset_type == "STOCK":
            market_counts[market] += 1
            total += 1
    return total, dict(market_counts)


def _is_kr_market_price_ready(market: str, market_counts: dict[str, int]) -> bool:
    if market not in KR_STOCK_MARKETS:
        return True
    return market_counts.get(market, 0) >= KR_STOCK_PRICE_READY_THRESHOLDS[market]


def _select_volume_rankings(
    loader: SupabaseLoader,
    target_date: date,
    market: str,
    kis_rows: list[dict[str, Any]],
    master_map: dict[str, dict[str, Any]],
    allow_price_fallback: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    threshold = VOLUME_THRESHOLDS[market]
    limit = RANKING_LIMITS[market]
    if len(kis_rows) >= threshold:
        return "KIS", kis_rows[:limit]

    if not allow_price_fallback:
        if kis_rows:
            logger.warning(
                f"{market} KIS volume ranking is sparse: count={len(kis_rows)}, threshold={threshold}, "
                "but price fallback is disabled because market price coverage is not ready. Retaining sparse KIS rows."
            )
            return "KIS", kis_rows[:limit]
        return "KIS", []
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
    logger.info(
        f"Starting daily ranking pipeline for {target_date:%Y-%m-%d} "
        f"(runner_date_utc={get_current_utc().date()}, target_date_kst={target_date})"
    )
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    skip, reason = should_skip_market_job(loader, target_date, "XKRX", "daily_ranking_pipeline")
    logger.info(f"xkrx_is_open={not skip} reason={reason}")
    if skip:
        previous_trading_day = get_previous_trading_day(loader, target_date, "XKRX")
        message = (
            f"XKRX market closed on {target_date:%Y-%m-%d}; skipped Korean market data ingestion. "
            f"previous_trading_day={previous_trading_day} reason={reason}"
        )
        logger.warning(message)
        if not dry_run:
            loader.insert_log(
                "daily_ranking_pipeline",
                target_date.strftime("%Y-%m-%d"),
                "SKIPPED_MARKET_CLOSED",
                0,
                message,
            )
        return
    master_map = _load_master_map(loader)
    kr_valid_rows, kr_market_counts = _count_valid_kr_stock_rows(loader, target_date, master_map)
    logger.info(
        f"target_date_valid_kr_stock_rows={kr_valid_rows} market_counts={kr_market_counts} "
        f"market_thresholds={KR_STOCK_PRICE_READY_THRESHOLDS}"
    )
    kr_market_ready = {
        market: _is_kr_market_price_ready(market, kr_market_counts)
        for market in KR_STOCK_MARKETS
    }
    skip_message_parts = []
    for market in ("KOSPI", "KOSDAQ"):
        if not kr_market_ready[market]:
            skip_message_parts.append(
                f"{market} valid_rows={kr_market_counts.get(market, 0)} threshold={KR_STOCK_PRICE_READY_THRESHOLDS[market]}"
            )
    skip_message = "; ".join(skip_message_parts)
    if skip_message:
        logger.warning(
            f"Per-market KR stock price coverage is insufficient on {target_date:%Y-%m-%d}; {skip_message}"
        )

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
                allow_price_fallback=kr_market_ready.get(market, True),
            )
            source_base_date = target_date.strftime("%Y-%m-%d") if source == "KIS" else (
                volume_rows[0]["raw_data"].get("source_base_date") if volume_rows else None
            )
            if market in KR_STOCK_MARKETS and source == "VALID_PRICE_FALLBACK" and source_base_date != target_date.strftime("%Y-%m-%d"):
                if not dry_run:
                    _delete_rankings(loader, target_date.strftime("%Y-%m-%d"), market, "volume")
                logger.warning(
                    f"Skipping {market} volume ranking because source_base_date={source_base_date} "
                    f"does not match target_date={target_date:%Y-%m-%d}."
                )
                continue
            if not dry_run:
                ranking_total += _persist_rankings(
                    loader,
                    target_date,
                    market,
                    "volume",
                    source,
                    volume_rows,
                    source_base_date=source_base_date,
                )
            else:
                logger.info(
                    f"[dry-run] {market} volume source={source} rows={len(volume_rows)} "
                    f"source_base_date={source_base_date}"
                )

        for market, limit in RANKING_LIMITS.items():
            if market in KR_STOCK_MARKETS and not kr_market_ready[market]:
                if not dry_run:
                    _delete_rankings(loader, target_date.strftime("%Y-%m-%d"), market, "trading_value")
                logger.warning(
                    f"Skipping {market} trading_value ranking because target-date stock prices are insufficient. "
                    f"valid_rows={kr_market_counts.get(market, 0)} threshold={KR_STOCK_PRICE_READY_THRESHOLDS[market]}"
                )
                continue
            source, rows, price_base_date = _build_price_based_rankings(
                loader, target_date, master_map, market, "trading_value", limit
            )
            if market in KR_STOCK_MARKETS and price_base_date != target_date.strftime("%Y-%m-%d"):
                if not dry_run:
                    _delete_rankings(loader, target_date.strftime("%Y-%m-%d"), market, "trading_value")
                logger.warning(
                    f"Skipping {market} trading_value ranking because source_base_date={price_base_date} "
                    f"does not match target_date={target_date:%Y-%m-%d}."
                )
                continue
            logger.info(f"{market} trading_value ranking price_base_date={price_base_date} source={source} rows={len(rows)}")
            if not dry_run:
                _persist_rankings(
                    loader,
                    target_date,
                    market,
                    "trading_value",
                    source,
                    rows,
                    source_base_date=price_base_date if source != "KIS" else target_date.strftime("%Y-%m-%d"),
                )

        for market, limit in RANKING_LIMITS.items():
            if market in KR_STOCK_MARKETS and not kr_market_ready[market]:
                if not dry_run:
                    _delete_rankings(loader, target_date.strftime("%Y-%m-%d"), market, "market_cap")
                logger.warning(
                    f"Skipping {market} market_cap ranking because target-date stock prices are insufficient. "
                    f"valid_rows={kr_market_counts.get(market, 0)} threshold={KR_STOCK_PRICE_READY_THRESHOLDS[market]}"
                )
                continue
            source, rows, price_base_date = _build_price_based_rankings(
                loader, target_date, master_map, market, "market_cap", limit
            )
            if market in KR_STOCK_MARKETS and price_base_date != target_date.strftime("%Y-%m-%d"):
                if not dry_run:
                    _delete_rankings(loader, target_date.strftime("%Y-%m-%d"), market, "market_cap")
                logger.warning(
                    f"Skipping {market} market_cap ranking because source_base_date={price_base_date} "
                    f"does not match target_date={target_date:%Y-%m-%d}."
                )
                continue
            logger.info(f"{market} market_cap ranking price_base_date={price_base_date} source={source} rows={len(rows)}")
            if not dry_run:
                _persist_rankings(
                    loader,
                    target_date,
                    market,
                    "market_cap",
                    source,
                    rows,
                    source_base_date=price_base_date if source != "KIS" else target_date.strftime("%Y-%m-%d"),
                )

        if not dry_run:
            loader.insert_log(
                "daily_ranking_pipeline",
                target_date.strftime("%Y-%m-%d"),
                "SKIPPED_INSUFFICIENT_PRICE_DATA" if skip_message else "SUCCESS",
                ranking_total,
                skip_message,
            )
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
        target_dt = parse_date_string(args.date)
    asyncio.run(run_pipeline(target_dt, dry_run=args.dry_run))
