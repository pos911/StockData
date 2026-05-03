from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date

from src.collectors.krx_collector import KRXCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import generate_available_at_for_eod, get_current_kst, parse_date_string

logger = get_logger(__name__)

MASTER_MARKETS = {"KOSPI", "KOSDAQ", "ETF", "ETN"}


def _raw_price_record(row: dict, target_date: date) -> dict:
    return {
        "source": "KRX",
        "symbol": row["symbol"],
        "base_date": row["base_date"],
        "raw_data": json.dumps(row, ensure_ascii=False),
        "available_at": generate_available_at_for_eod(target_date).isoformat(),
    }


def _master_record(
    symbol: str,
    name: str,
    market: str,
    asset_type: str,
    existing_active_map: dict[str, bool],
    protected_symbols: set[str],
) -> dict:
    normalized_symbol = normalize_symbol_value(symbol)
    if normalized_symbol in protected_symbols:
        is_active = True
    elif normalized_symbol in existing_active_map:
        is_active = bool(existing_active_map[normalized_symbol])
    else:
        is_active = False
    return {
        "symbol": normalized_symbol,
        "name": name,
        "market": market,
        "asset_type": asset_type,
        "is_active": is_active,
        "updated_at": get_current_kst().isoformat(),
    }


def _load_existing_active_map(loader: SupabaseLoader) -> dict[str, bool]:
    try:
        rows = (
            loader.client.table("stocks_master")
            .select("symbol, is_active")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.warning(f"Failed to load existing stocks_master active map: {exc}")
        return {}
    return {
        normalize_symbol_value(row.get("symbol")): bool(row.get("is_active"))
        for row in rows
        if row.get("symbol")
    }


def _collect_krx_stock_master(
    krx: KRXCollector,
    target_date: date,
    existing_active_map: dict[str, bool],
    protected_symbols: set[str],
) -> list[dict]:
    rows = krx.fetch_kind_listings()
    if len(rows) < 1500:
        logger.warning(f"KRX KIND listing count too small ({len(rows)}); falling back to FDR.")
        rows = krx.fetch_fdr_krx_listings()
    if len(rows) < 1500:
        logger.warning(f"FDR KRX listing count too small ({len(rows)}); falling back to pykrx.")
        rows = krx.fetch_pykrx_market_listings(target_date)

    master_rows = []
    for row in rows:
        market = row.get("market")
        if market not in {"KOSPI", "KOSDAQ"}:
            continue
        symbol = normalize_symbol_value(row.get("code"))
        if not symbol:
            continue
        master_rows.append(
            _master_record(
                symbol,
                row.get("name") or symbol,
                market,
                "STOCK",
                existing_active_map,
                protected_symbols,
            )
        )
    return master_rows


def _collect_krx_etp_master_and_prices(
    krx: KRXCollector,
    target_date: date,
    market: str,
    asset_type: str,
    rows: list[dict],
    existing_active_map: dict[str, bool],
    protected_symbols: set[str],
):
    master_rows = []
    raw_price_rows = []
    normalized_price_rows = []
    for row in rows:
        normalized = krx.normalize_krx_etp_row(row, market=market, asset_type=asset_type, target_date=target_date)
        symbol = normalized.get("symbol")
        if not symbol:
            continue
        master_rows.append(
            _master_record(
                symbol,
                normalized.get("name") or symbol,
                market,
                asset_type,
                existing_active_map,
                protected_symbols,
            )
        )
        raw_price_rows.append(_raw_price_record(normalized, target_date))
        if normalized.get("close_price") is not None:
            normalized_price_rows.append(
                {
                    "symbol": normalized["symbol"],
                    "base_date": normalized["base_date"],
                    "open_price": normalized["open_price"],
                    "high_price": normalized["high_price"],
                    "low_price": normalized["low_price"],
                    "close_price": normalized["close_price"],
                    "volume": normalized["volume"],
                    "trading_value": normalized["trading_value"],
                    "market_cap": normalized["market_cap"],
                    "outstanding_shares": normalized["outstanding_shares"],
                    "available_at": normalized["available_at"],
                }
            )
    return master_rows, raw_price_rows, normalized_price_rows


def _load_static_enabled_symbols(loader: SupabaseLoader) -> set[str]:
    try:
        rows = (
            loader.client.table("static_stock_universe")
            .select("symbol")
            .eq("enabled", True)
            .execute()
            .data
            or []
        )
        return {normalize_symbol_value(row.get("symbol")) for row in rows if row.get("symbol")}
    except Exception as exc:
        logger.warning(f"Failed to load static protected symbols: {exc}")
        return set()


def _deactivate_missing_master_rows(loader: SupabaseLoader, current_symbols: set[str], protected_symbols: set[str]) -> int:
    try:
        existing_rows = (
            loader.client.table("stocks_master")
            .select("symbol, market, is_active")
            .in_("market", list(MASTER_MARKETS))
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.warning(f"Failed to load existing stocks_master rows for deactivate step: {exc}")
        return 0

    deactivated = 0
    for row in existing_rows:
        symbol = normalize_symbol_value(row.get("symbol"))
        market = row.get("market")
        if not symbol or market not in MASTER_MARKETS:
            continue
        if symbol in current_symbols or symbol in protected_symbols:
            continue
        if row.get("is_active") is False:
            continue
        loader.update_record(
            "stocks_master",
            {"symbol": symbol},
            {"is_active": False, "updated_at": get_current_kst().isoformat()},
        )
        deactivated += 1
    return deactivated


def run_pipeline(target_date: date) -> None:
    logger.info(f"Starting daily master pipeline for {target_date:%Y-%m-%d}")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    krx = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    protected_symbols = _load_static_enabled_symbols(loader)
    existing_active_map = _load_existing_active_map(loader)

    stock_master_rows = _collect_krx_stock_master(krx, target_date, existing_active_map, protected_symbols)
    etf_rows = krx.fetch_etf_daily_trading(target_date)
    etn_rows = krx.fetch_etn_daily_trading(target_date)

    etf_master_rows, etf_raw_prices, etf_normalized_prices = _collect_krx_etp_master_and_prices(
        krx, target_date, "ETF", "ETF", etf_rows, existing_active_map, protected_symbols
    )
    etn_master_rows, etn_raw_prices, etn_normalized_prices = _collect_krx_etp_master_and_prices(
        krx, target_date, "ETN", "ETN", etn_rows, existing_active_map, protected_symbols
    )

    all_master_rows = stock_master_rows + etf_master_rows + etn_master_rows
    deduped_master = {}
    for row in all_master_rows:
        symbol = row["symbol"]
        existing = deduped_master.get(symbol)
        if not existing:
            deduped_master[symbol] = row
            continue
        if existing.get("market") in {"ETF", "ETN"} and row.get("market") in {"KOSPI", "KOSDAQ"}:
            continue
        deduped_master[symbol] = row

    master_rows = list(deduped_master.values())
    loader.upsert_records("stocks_master", master_rows)
    if etf_raw_prices:
        loader.upsert_records("raw_stock_prices_daily", etf_raw_prices)
    if etn_raw_prices:
        loader.upsert_records("raw_stock_prices_daily", etn_raw_prices)
    if etf_normalized_prices:
        loader.upsert_records("normalized_stock_prices_daily", etf_normalized_prices)
    if etn_normalized_prices:
        loader.upsert_records("normalized_stock_prices_daily", etn_normalized_prices)

    current_symbols = {row["symbol"] for row in master_rows}
    deactivated_count = _deactivate_missing_master_rows(loader, current_symbols, protected_symbols)

    counter = Counter(row["market"] for row in master_rows)
    duplicate_count = len(all_master_rows) - len(master_rows)
    unknown_market_count = sum(1 for row in master_rows if row.get("market") not in MASTER_MARKETS)

    logger.info(f"KOSPI count: {counter.get('KOSPI', 0)}")
    logger.info(f"KOSDAQ count: {counter.get('KOSDAQ', 0)}")
    logger.info(f"ETF count: {counter.get('ETF', 0)}")
    logger.info(f"ETN count: {counter.get('ETN', 0)}")
    logger.info(f"total master count: {len(master_rows)}")
    logger.info(f"duplicate symbol count: {duplicate_count}")
    logger.info(f"unknown market count: {unknown_market_count}")
    logger.info(f"static protected count: {len(protected_symbols)}")
    logger.info(f"deactivated count: {deactivated_count}")

    loader.insert_log("daily_master_pipeline", target_date.strftime("%Y-%m-%d"), "SUCCESS", len(master_rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format (default: today)")
    args = parser.parse_args()
    target_dt = get_current_kst().date()
    if args.date:
        target_dt = parse_date_string(args.date)
    run_pipeline(target_dt)
