from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import json
import os

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.market_data_quality import get_latest_valid_price_date, is_valid_price_row
from src.utils.trading_calendar import (
    get_market_calendar_diagnostic,
    get_latest_trading_day_on_or_before,
    get_next_trading_day,
    get_previous_trading_day,
    is_market_open,
)
from src.utils.config_loader import load_config
from src.utils.time_utils import get_current_utc, get_kst_target_date
from src.utils.symbols import is_q_prefixed_numeric_symbol, normalize_symbol_value


DATE_COLUMNS = {
    "normalized_stock_prices_daily": "base_date",
    "normalized_stock_supply_daily": "base_date",
    "normalized_stock_short_selling": "base_date",
    "normalized_stock_snapshots_daily": "base_date",
    "normalized_market_rankings_daily": "base_date",
    "normalized_stock_fundamentals_ratios": "base_date",
    "normalized_stock_events_daily": "base_date",
    "normalized_macro_series": "base_date",
    "normalized_global_macro_daily": "base_date",
    "market_breadth_daily": "base_date",
    "feature_store_daily": "base_date",
    "raw_stock_prices_daily": "base_date",
    "raw_stock_supply_daily": "base_date",
    "raw_stock_short_selling": "base_date",
    "raw_market_rankings": "base_date",
    "raw_ecos_macro_daily": "date",
    "stocks_master": "updated_at",
    "macro_series_master": "updated_at",
}

LEGACY_TABLES = {"normalized_stock_fundamentals"}
CORE_MACRO_SERIES = ["KR_GOVT_10Y", "KR_GOVT_3Y", "USDKRW", "KR_CD_91D", "KR_CORP_AA_3Y"]
SYMBOL_QUALITY_TABLES = [
    "feature_store_daily",
    "normalized_stock_prices_daily",
    "raw_stock_prices_daily",
    "normalized_stock_supply_daily",
    "raw_stock_supply_daily",
    "normalized_stock_short_selling",
    "normalized_stock_snapshots_daily",
    "normalized_market_rankings_daily",
    "raw_market_rankings",
]
EXPECTED_RANKINGS = {
    ("KOSPI", "volume"),
    ("KOSPI", "trading_value"),
    ("KOSDAQ", "volume"),
    ("KOSDAQ", "trading_value"),
    ("KOSDAQ", "market_cap"),
    ("ETF", "volume"),
    ("ETF", "trading_value"),
}
DEFAULT_REPORT_REQUIRED_ETF_SYMBOLS = ("396500", "305720")
KRX_MARKET_TABLES = {
    "normalized_stock_prices_daily",
    "normalized_stock_supply_daily",
    "normalized_stock_short_selling",
    "normalized_stock_snapshots_daily",
    "normalized_market_rankings_daily",
    "normalized_stock_fundamentals_ratios",
    "normalized_stock_events_daily",
    "raw_stock_prices_daily",
    "raw_stock_supply_daily",
    "raw_stock_short_selling",
    "raw_market_rankings",
}


def _standardize_market_value(market: str | None) -> str | None:
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
    return value


def _count(loader: SupabaseLoader, table: str) -> int:
    res = loader.client.table(table).select("*", count="exact").limit(1).execute()
    return int(res.count or 0)


def _latest(loader: SupabaseLoader, table: str, date_col: str) -> str | None:
    res = loader.client.table(table).select(date_col).order(date_col, desc=True).limit(1).execute()
    if not res.data:
        return None
    value = res.data[0].get(date_col)
    return str(value)[:10] if value else None


def _target_count(loader: SupabaseLoader, table: str, date_col: str, target_date: str) -> int:
    if date_col == "updated_at":
        return 0
    res = loader.client.table(table).select(date_col, count="exact").eq(date_col, target_date).limit(1).execute()
    return int(res.count or 0)


def _active_symbols(loader: SupabaseLoader) -> set[str]:
    rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    return {row["symbol"] for row in rows if row.get("is_active") and row.get("symbol")}


def _symbol_coverage(loader: SupabaseLoader, table: str, target_date: str, active: set[str]) -> tuple[float | None, int]:
    if table not in {
        "normalized_stock_prices_daily",
        "normalized_stock_supply_daily",
        "feature_store_daily",
        "normalized_stock_fundamentals_ratios",
    }:
        return None, 0
    rows = loader.fetch_all(table, "base_date", target_date, target_date)
    if table == "normalized_stock_prices_daily":
        rows = [row for row in rows if is_valid_price_row(row, market_is_open=True)]
    covered = {row["symbol"] for row in rows if row.get("symbol") in active}
    missing = len(active - covered)
    coverage = len(covered) / max(len(active), 1)
    return coverage, missing


def _stale_days(latest_date: str | None, today: date) -> int | None:
    if not latest_date:
        return None
    try:
        return (today - date.fromisoformat(latest_date[:10])).days
    except ValueError:
        return None


def _status(
    table: str,
    row_count: int,
    target_count: int,
    stale: int | None,
    coverage: float | None,
    xkrx_is_open: bool,
) -> tuple[str, str]:
    if table in LEGACY_TABLES:
        return "LEGACY", "legacy table; not required for current report path"
    if row_count == 0:
        return "FAIL_EMPTY", "table has no rows"
    if stale is None:
        return "FAIL_SCHEMA", "date column could not be interpreted"
    if table == "normalized_stock_short_selling":
        if stale > 5:
            return "WARN_STALE", "short-selling can be sparse, but latest data is stale"
        return "SUCCESS", "short-selling table has recent rows"
    if not xkrx_is_open and table in KRX_MARKET_TABLES:
        if target_count > 0:
            return "WARN_CLOSED_MARKET_ROWS", "rows exist on an XKRX closed date"
        return "OK_CLOSED_MARKET_NO_ROWS", "target date is an XKRX closed date"
    if coverage is not None:
        if table == "normalized_stock_prices_daily" and coverage < 0.7:
            return "WARN_COVERAGE", f"active symbol coverage={coverage:.1%}"
        if table == "normalized_stock_supply_daily" and coverage < 0.4:
            return "WARN_COVERAGE", f"active symbol coverage={coverage:.1%}"
    if stale > 5:
        return "WARN_STALE", f"latest row is {stale} days old"
    if target_count == 0 and stale > 0:
        return "WARN_NO_TARGET_ROWS", "no rows for target date"
    return "SUCCESS", "ok"


def _load_report_required_etf_symbols(loader: SupabaseLoader) -> list[str]:
    symbols: set[str] = set()
    try:
        rows = (
            loader.client.table("static_stock_universe")
            .select("symbol, market")
            .eq("enabled", True)
            .execute()
            .data
            or []
        )
        for row in rows:
            if _standardize_market_value(row.get("market")) == "ETF":
                symbol = normalize_symbol_value(row.get("symbol"))
                if symbol:
                    symbols.add(symbol)
    except Exception:
        pass

    if os.path.exists("config/stock_universe.json"):
        try:
            with open("config/stock_universe.json", "r", encoding="utf-8") as handle:
                items = json.load(handle)
            for item in items:
                if item.get("enabled", True) and _standardize_market_value(item.get("market")) == "ETF":
                    symbol = normalize_symbol_value(item.get("symbol"))
                    if symbol:
                        symbols.add(symbol)
        except Exception:
            pass
    if not symbols:
        symbols.update(DEFAULT_REPORT_REQUIRED_ETF_SYMBOLS)
    return sorted(symbols)


def _macro_series_status(loader: SupabaseLoader, today: date):
    print("\n=== Core Macro Series Freshness ===")
    print(f"{'series_id':<20} | {'latest_date':<12} | {'stale_days':<10} | {'status':<12}")
    print("-" * 65)
    for series_id in CORE_MACRO_SERIES:
        res = (
            loader.client.table("normalized_macro_series")
            .select("base_date")
            .eq("series_id", series_id)
            .order("base_date", desc=True)
            .limit(1)
            .execute()
        )
        latest = str(res.data[0]["base_date"])[:10] if res.data else None
        stale = _stale_days(latest, today)
        if stale is None:
            status = "FAIL_EMPTY"
        elif series_id in {"KR_GOVT_10Y", "USDKRW"} and stale > 5:
            status = "FAIL_STALE"
        elif series_id in {"KR_GOVT_10Y", "USDKRW"} and stale > 2:
            status = "WARN_STALE"
        elif stale > 5:
            status = "WARN_STALE"
        else:
            status = "SUCCESS"
        print(f"{series_id:<20} | {latest or 'N/A':<12} | {str(stale):<10} | {status:<12}")


def _print_price_quality(loader: SupabaseLoader):
    print("\n=== PRICE TABLE QUALITY ===")
    latest = _latest(loader, "normalized_stock_prices_daily", "base_date")
    if not latest:
        print("latest_price_date=N/A status=FAIL_NO_VALID_PRICE_ROWS note=no price rows")
        return
    rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest, latest)
    latest_total_rows = len(rows)
    null_close_rows = sum(1 for row in rows if row.get("close_price") in (None, ""))
    null_volume_rows = sum(1 for row in rows if row.get("volume") in (None, ""))
    null_trading_value_rows = sum(1 for row in rows if row.get("trading_value") in (None, ""))
    snapshot_only_rows = sum(
        1
        for row in rows
        if all(row.get(field) in (None, "") for field in ("open_price", "high_price", "low_price", "close_price", "volume", "trading_value"))
    )
    valid_price_rows = sum(
        1
        for row in rows
        if is_valid_price_row(row, market_is_open=True)
    )
    if snapshot_only_rows > 0:
        status = "FAIL_PRICE_QUALITY"
    elif valid_price_rows == 0:
        status = "FAIL_NO_VALID_PRICE_ROWS"
    elif latest_total_rows and valid_price_rows / latest_total_rows < 0.8:
        status = "WARN_PRICE_NULL_RATIO"
    else:
        status = "SUCCESS"
    note = (
        f"null_close={null_close_rows}, null_volume={null_volume_rows}, "
        f"null_trading_value={null_trading_value_rows}"
    )
    print(f"latest_price_date={latest}")
    print(f"latest_total_rows={latest_total_rows}")
    print(f"null_close_rows={null_close_rows}")
    print(f"null_volume_rows={null_volume_rows}")
    print(f"null_trading_value_rows={null_trading_value_rows}")
    print(f"snapshot_only_rows={snapshot_only_rows}")
    print(f"valid_price_rows={valid_price_rows}")
    print(f"status={status}")
    print(f"note={note}")


def _raw_price_value(raw_payload: Any, *keys: str) -> Any:
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            raw_payload = {}
    if not isinstance(raw_payload, dict):
        return None
    candidates = [raw_payload]
    if isinstance(raw_payload.get("response_row"), dict):
        candidates.insert(0, raw_payload["response_row"])
    for payload in candidates:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return None


def fetch_latest_normalized_macro_value(loader: SupabaseLoader, series_id: str, target_date: date):
    try:
        response = (
            loader.client.table("normalized_macro_series")
            .select("value, base_date")
            .eq("series_id", series_id)
            .lte("base_date", target_date.isoformat())
            .order("base_date", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
    except Exception as exc:
        print(f"normalized_macro_series lookup failed for {series_id}: {exc}")
    return None


def _print_price_mapping_quality(loader: SupabaseLoader):
    print("\n=== RAW/NORMALIZED PRICE FIELD QUALITY ===")
    raw_latest = _latest(loader, "raw_stock_prices_daily", "base_date")
    normalized_latest = _latest(loader, "normalized_stock_prices_daily", "base_date")
    master_market = {}
    try:
        master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
        master_market = {row.get("symbol"): row.get("market") for row in master_rows if row.get("symbol")}
    except Exception as exc:
        print(f"stocks_master market lookup status=WARN note={exc}")

    if raw_latest:
        raw_rows = loader.fetch_all("raw_stock_prices_daily", "base_date", raw_latest, raw_latest)
        print(
            "raw_stock_prices_daily "
            f"base_date={raw_latest} total_rows={len(raw_rows)} "
            f"valid_close_rows={sum(1 for row in raw_rows if _raw_price_value(row.get('raw_data'), 'stck_clpr', 'close_price') not in (None, ''))} "
            f"valid_volume_rows={sum(1 for row in raw_rows if _raw_price_value(row.get('raw_data'), 'acml_vol', 'volume') not in (None, ''))} "
            f"valid_trading_value_rows={sum(1 for row in raw_rows if _raw_price_value(row.get('raw_data'), 'acml_tr_pbmn', 'trading_value') not in (None, ''))} "
            f"market_not_null_rows={sum(1 for row in raw_rows if master_market.get(row.get('symbol')))}"
        )
    else:
        print("raw_stock_prices_daily base_date=N/A status=FAIL_EMPTY")

    if normalized_latest:
        rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", normalized_latest, normalized_latest)
        print(
            "normalized_stock_prices_daily "
            f"base_date={normalized_latest} total_rows={len(rows)} "
            f"valid_close_rows={sum(1 for row in rows if row.get('close_price') not in (None, ''))} "
            f"valid_volume_rows={sum(1 for row in rows if row.get('volume') not in (None, ''))} "
            f"valid_trading_value_rows={sum(1 for row in rows if row.get('trading_value') not in (None, ''))} "
            f"market_not_null_rows={sum(1 for row in rows if master_market.get(row.get('symbol')))}"
        )
    else:
        print("normalized_stock_prices_daily base_date=N/A status=FAIL_EMPTY")


def _print_ratio_and_short_quality(loader: SupabaseLoader):
    print("\n=== RATIO / SHORT SELLING QUALITY ===")
    ratio_latest = _latest(loader, "normalized_stock_fundamentals_ratios", "base_date")
    if ratio_latest:
        ratio_rows = loader.fetch_all("normalized_stock_fundamentals_ratios", "base_date", ratio_latest, ratio_latest)
        total = len(ratio_rows)
        zero_fields = {
            field: sum(1 for row in ratio_rows if row.get(field) == 0)
            for field in ("per", "pbr", "roe", "debt_ratio")
        }
        print(f"fundamental_ratios base_date={ratio_latest} total_rows={total} zero_counts={zero_fields}")
    else:
        print("fundamental_ratios base_date=N/A status=LEGACY_OR_EMPTY")

    short_latest = _latest(loader, "normalized_stock_short_selling", "base_date")
    if short_latest:
        short_rows = loader.fetch_all("normalized_stock_short_selling", "base_date", short_latest, short_latest)
        suspicious = [
            row
            for row in short_rows
            if (row.get("short_volume") or 0) > 0
            and (row.get("short_value") or 0) > 0
            and row.get("short_ratio") in (None, "", 0)
        ]
        print(
            f"short_selling base_date={short_latest} total_rows={len(short_rows)} "
            f"positive_value_missing_ratio_rows={len(suspicious)}"
        )
    else:
        print("short_selling base_date=N/A status=WARN_EMPTY")


def _fetch_symbols_for_table(loader: SupabaseLoader, table_name: str) -> list[str]:
    try:
        rows = loader.client.table(table_name).select("symbol").limit(5000).execute().data or []
        return [row.get("symbol") for row in rows if row.get("symbol")]
    except Exception as exc:
        print(f"{table_name} symbol scan failed: {exc}")
        return []


def _print_symbol_quality(loader: SupabaseLoader):
    print("\n=== SYMBOL QUALITY ===")
    q_prefix_rows_by_table = {}
    q_prefix_samples = {}
    duplicate_candidates = set()

    for table_name in SYMBOL_QUALITY_TABLES:
        symbols = _fetch_symbols_for_table(loader, table_name)
        q_rows = [symbol for symbol in symbols if is_q_prefixed_numeric_symbol(symbol)]
        q_symbols = sorted(set(q_rows))
        q_prefix_rows_by_table[table_name] = len(q_rows)
        q_prefix_samples[table_name] = q_symbols[:5]
        symbol_set = set(symbols)
        for q_symbol in q_symbols:
            canonical = normalize_symbol_value(q_symbol)
            if canonical in symbol_set:
                duplicate_candidates.add(f"{canonical} / {q_symbol}")

    status = "FAIL_SYMBOL_NORMALIZATION" if any(count > 0 for count in q_prefix_rows_by_table.values()) else "SUCCESS"
    print(f"q_prefix_rows_by_table={q_prefix_rows_by_table}")
    print(f"canonical_duplicate_candidates={sorted(duplicate_candidates)}")
    print(f"q_prefix_samples={q_prefix_samples}")
    print(f"status={status}")


def _print_market_ranking_quality(loader: SupabaseLoader):
    print("\n=== MARKET RANKING QUALITY ===")
    latest = _latest(loader, "normalized_market_rankings_daily", "base_date")
    if not latest:
        print("status=FAIL_RANKING_EMPTY note=no ranking rows")
        return

    ranking_rows = loader.fetch_all("normalized_market_rankings_daily", "base_date", latest, latest)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    master_map = {row["symbol"]: row for row in master_rows if row.get("symbol")}
    latest_dt = date.fromisoformat(latest)
    latest_price_day = get_latest_trading_day_on_or_before(loader, latest_dt, "XKRX")
    price_rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest, latest)
    kr_valid_rows = 0
    for price_row in price_rows:
        master = master_map.get(normalize_symbol_value(price_row.get("symbol")))
        if not master:
            continue
        if _standardize_market_value(master.get("market")) in {"KOSPI", "KOSDAQ"} and master.get("asset_type") == "STOCK":
            if is_valid_price_row(price_row, market_is_open=True):
                kr_valid_rows += 1
    combos = {(row.get("market"), row.get("rank_type")) for row in ranking_rows}
    missing_expected_rankings = sorted(f"{market}:{rank_type}" for market, rank_type in EXPECTED_RANKINGS if (market, rank_type) not in combos)

    summary = {}
    for row in ranking_rows:
        key = (row.get("market"), row.get("rank_type"))
        bucket = summary.setdefault(
            key,
            {"row_count": 0, "no_master_rows": 0, "market_mismatch_rows": 0, "q_prefix_rows": 0},
        )
        bucket["row_count"] += 1
        symbol = row.get("symbol")
        if is_q_prefixed_numeric_symbol(symbol):
            bucket["q_prefix_rows"] += 1
        master = master_map.get(symbol)
        if not master:
            bucket["no_master_rows"] += 1
            continue
        ranking_market = _standardize_market_value(row.get("market"))
        master_market = _standardize_market_value(master.get("market"))
        if ranking_market != "KOSPI200" and master_market != ranking_market:
            bucket["market_mismatch_rows"] += 1

    for (market, rank_type), stats in sorted(summary.items()):
        print(
            f"ranking_market={market} rank_type={rank_type} row_count={stats['row_count']} "
            f"no_master_rows={stats['no_master_rows']} market_mismatch_rows={stats['market_mismatch_rows']} "
            f"q_prefix_rows={stats['q_prefix_rows']}"
        )
    print(f"missing_expected_rankings={missing_expected_rankings}")

    if any(stats["q_prefix_rows"] > 0 for stats in summary.values()):
        print("status=FAIL_SYMBOL_NORMALIZATION")
    elif any(stats["market_mismatch_rows"] > 0 for (market, _), stats in summary.items() if market == "KOSPI"):
        print("status=FAIL_RANKING_MARKET_MISMATCH")
    elif latest_price_day and latest == latest_price_day.isoformat() and kr_valid_rows < 100:
        print("status=OK_SKIPPED_INSUFFICIENT_PRICE_DATA")
    elif ("KOSDAQ", "volume") not in combos:
        print("status=FAIL_RANKING_KOSDAQ")
    elif ("KOSDAQ", "trading_value") not in combos:
        print("status=WARN_RANKING_KOSDAQ_TRADING_VALUE")
    elif ("ETF", "trading_value") not in combos:
        print("status=WARN_RANKING_ETF_TRADING_VALUE")
    else:
        print("status=SUCCESS")


def _print_market_master_quality(loader: SupabaseLoader):
    print("\n=== MARKET MASTER QUALITY ===")
    rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    counts = {"KOSPI": 0, "KOSDAQ": 0, "ETF": 0, "ETN": 0}
    unknown_market_count = 0
    q_prefix_count = 0
    symbols = []
    for row in rows:
        symbol = row.get("symbol")
        market = _standardize_market_value(row.get("market"))
        if symbol:
            symbols.append(symbol)
            if is_q_prefixed_numeric_symbol(symbol):
                q_prefix_count += 1
        if market in counts:
            counts[market] += 1
        else:
            unknown_market_count += 1
    duplicate_symbol_count = len(symbols) - len(set(symbols))
    print(f"KOSPI count={counts['KOSPI']}")
    print(f"KOSDAQ count={counts['KOSDAQ']}")
    print(f"ETF count={counts['ETF']}")
    print(f"ETN count={counts['ETN']}")
    print(f"unknown market count={unknown_market_count}")
    print(f"duplicate symbol count={duplicate_symbol_count}")
    print(f"q_prefix count={q_prefix_count}")
    if q_prefix_count > 0:
        print("status=FAIL_SYMBOL_NORMALIZATION")
    elif counts["KOSDAQ"] < 1000:
        print("status=FAIL_KOSDAQ_MASTER")
    elif counts["ETF"] < 100:
        print("status=WARN_ETF_MASTER")
    elif counts["ETN"] == 0:
        print("status=WARN_ETN_MASTER")
    else:
        print("status=SUCCESS")


def _print_ranking_source_quality(loader: SupabaseLoader, today: date):
    print("\n=== RANKING SOURCE QUALITY ===")
    latest = _latest(loader, "normalized_market_rankings_daily", "base_date")
    if not latest:
        print("status=FAIL_RANKING_EMPTY")
        return
    quality = get_latest_valid_price_date(loader, today, lookback_days=10, min_valid_rows=100)
    ranking_rows = loader.fetch_all("normalized_market_rankings_daily", "base_date", latest, latest)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    master_map = {row["symbol"]: row for row in master_rows if row.get("symbol")}
    target_price_rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest, latest)
    kr_valid_rows = 0
    for price_row in target_price_rows:
        master = master_map.get(normalize_symbol_value(price_row.get("symbol")))
        if not master:
            continue
        if _standardize_market_value(master.get("market")) in {"KOSPI", "KOSDAQ"} and master.get("asset_type") == "STOCK":
            if is_valid_price_row(price_row, market_is_open=True):
                kr_valid_rows += 1
    mismatch_rows = 0
    q_prefix_rows = 0
    volume_counts_by_source = {"KOSPI": {}, "KOSDAQ": {}, "ETF": {}, "ETN": {}}
    volume_total = {"KOSPI": 0, "KOSDAQ": 0, "ETF": 0, "ETN": 0}
    trading_value_counts = {}
    market_cap_counts = {}
    for row in ranking_rows:
        symbol = row.get("symbol")
        if is_q_prefixed_numeric_symbol(symbol):
            q_prefix_rows += 1
        master = master_map.get(symbol)
        ranking_market = _standardize_market_value(row.get("market"))
        master_market = _standardize_market_value(master.get("market")) if master else None
        if ranking_market != "KOSPI200" and master and master_market != ranking_market:
            mismatch_rows += 1
        if row.get("rank_type") == "volume" and ranking_market in volume_counts_by_source:
            source = row.get("source") or "UNKNOWN"
            volume_counts_by_source[ranking_market][source] = volume_counts_by_source[ranking_market].get(source, 0) + 1
            volume_total[ranking_market] += 1
        if row.get("rank_type") == "trading_value":
            trading_value_counts[ranking_market] = trading_value_counts.get(ranking_market, 0) + 1
        if row.get("rank_type") == "market_cap":
            market_cap_counts[ranking_market] = market_cap_counts.get(ranking_market, 0) + 1
    stale_days = _stale_days(latest, today)
    print(f"latest ranking date={latest}")
    print(f"KOSPI volume count by source={volume_counts_by_source['KOSPI']}")
    print(f"KOSDAQ volume count by source={volume_counts_by_source['KOSDAQ']}")
    print(f"ETF volume count by source={volume_counts_by_source['ETF']}")
    print(f"ETN volume count by source={volume_counts_by_source['ETN']}")
    print(f"KOSDAQ volume total count={volume_total['KOSDAQ']}")
    print(f"trading_value ranking count by market={trading_value_counts}")
    print(f"market_cap ranking count by market={market_cap_counts}")
    print(f"market mismatch rows={mismatch_rows}")
    print(f"q_prefix rows={q_prefix_rows}")
    print(f"stale ranking rows={0 if stale_days is None else stale_days}")
    print(f"selected valid price date={quality.get('selected_price_base_date')}")
    if latest == today.isoformat() and kr_valid_rows < 100:
        print("status=OK_SKIPPED_INSUFFICIENT_PRICE_DATA")
    elif volume_total["KOSDAQ"] == 0:
        print("status=FAIL_KOSDAQ_VOLUME_RANK")
    elif mismatch_rows > 0:
        print("status=FAIL_RANKING_MARKET_MISMATCH")
    elif q_prefix_rows > 0:
        print("status=FAIL_SYMBOL_NORMALIZATION")
    elif volume_total["KOSPI"] < 20:
        print("status=WARN_KOSPI_VOLUME_SPARSE")
    elif volume_total["KOSDAQ"] < 20:
        print("status=WARN_KOSDAQ_VOLUME_FALLBACK")
    else:
        print("status=SUCCESS")


def _print_macro_quality(loader: SupabaseLoader, today: date):
    print("\n=== MACRO QUALITY ===")
    latest = _latest(loader, "normalized_global_macro_daily", "base_date")
    if not latest:
        print("status=FAIL_GLOBAL_MACRO_EMPTY")
        return
    schema_ok = True
    try:
        row = (
            loader.client.table("normalized_global_macro_daily")
            .select("base_date, us10y, us3y, kr10y, dxy, usdkrw")
            .order("base_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:
        schema_ok = False
        print(f"normalized_global_macro_daily latest base_date={latest}")
        print(f"schema_error={exc}")
        print("status=WARN_SCHEMA_US3Y_MISSING")
        row = []
    latest_row = row[0] if row else {}
    dgs3 = fetch_latest_normalized_macro_value(loader, "DGS3", today)
    print(f"normalized_global_macro_daily latest base_date={latest}")
    print(f"us10y value={latest_row.get('us10y')}")
    print(f"us3y value={latest_row.get('us3y')}")
    print(f"kr10y value={latest_row.get('kr10y')}")
    print(f"us3y null 여부={latest_row.get('us3y') is None}")
    print(f"normalized_macro_series DGS3 latest value={dgs3.get('value') if dgs3 else None}")
    if not schema_ok:
        return
    if not dgs3:
        print("status=WARN_MACRO_DGS3_MISSING")
    elif latest_row.get("us3y") is None:
        print("status=WARN_MACRO_US3Y_NULL")
    else:
        print("status=SUCCESS")


def _print_market_calendar_quality(loader: SupabaseLoader, today: date):
    print("\n=== MARKET CALENDAR QUALITY ===")
    try:
        loader.client.table("market_trading_calendar").select(
            "calendar_date",
            count="exact",
        ).limit(1).execute()
        current_year = loader.fetch_all("market_trading_calendar", "calendar_date", f"{today.year}-01-01", f"{today.year}-12-31")
    except Exception as exc:
        print(f"table_exists=False note={exc}")
        print("status=WARN_MARKET_CALENDAR_MISSING")
        return

    next_year = loader.fetch_all("market_trading_calendar", "calendar_date", f"{today.year + 1}-01-01", f"{today.year + 1}-12-31")
    recent_rows = loader.fetch_all("market_trading_calendar", "calendar_date", (today - timedelta(days=30)).isoformat(), today.isoformat())
    upcoming_rows = loader.fetch_all("market_trading_calendar", "calendar_date", today.isoformat(), (today + timedelta(days=30)).isoformat())

    def _exchange_rows(rows, exchange_code):
        return [row for row in rows if row.get("exchange_code") == exchange_code]

    def _range(rows):
        if not rows:
            return None, None
        dates = sorted(str(row.get("calendar_date"))[:10] for row in rows if row.get("calendar_date"))
        return (dates[0], dates[-1]) if dates else (None, None)

    xkrx_current = _exchange_rows(current_year, "XKRX")
    xkrx_next = _exchange_rows(next_year, "XKRX")
    xnys_current = _exchange_rows(current_year, "XNYS")
    xnys_next = _exchange_rows(next_year, "XNYS")
    xkrx_today_rows = _exchange_rows(
        loader.fetch_all("market_trading_calendar", "calendar_date", today.isoformat(), today.isoformat()),
        "XKRX",
    )
    xnys_today_rows = _exchange_rows(
        loader.fetch_all("market_trading_calendar", "calendar_date", today.isoformat(), today.isoformat()),
        "XNYS",
    )
    xkrx_weekday_holidays = sum(1 for row in xkrx_current if not row.get("is_open") and row.get("reason") == "holiday")
    xnys_weekday_holidays = sum(1 for row in xnys_current if not row.get("is_open") and row.get("reason") == "holiday")
    xkrx_previous = get_previous_trading_day(loader, today, "XKRX")
    xkrx_next_day = get_next_trading_day(loader, today, "XKRX")
    xnys_previous = get_previous_trading_day(loader, today, "XNYS")
    xnys_next_day = get_next_trading_day(loader, today, "XNYS")
    xkrx_diag = get_market_calendar_diagnostic(loader, today, "XKRX")
    xnys_diag = get_market_calendar_diagnostic(loader, today, "XNYS")
    override_rows = [
        row for row in (current_year + next_year)
        if row.get("source") == "manual_override"
    ]
    recent_override_rows = [
        row for row in override_rows
        if (today - timedelta(days=30)).isoformat() <= str(row.get("calendar_date"))[:10] <= (today + timedelta(days=30)).isoformat()
    ]
    xkrx_recent_open_count = sum(1 for row in _exchange_rows(recent_rows, "XKRX") if row.get("is_open"))
    xkrx_next_open_count = sum(1 for row in _exchange_rows(upcoming_rows, "XKRX") if row.get("is_open"))
    xkrx_min, xkrx_max = _range(xkrx_current + xkrx_next)
    xnys_min, xnys_max = _range(xnys_current + xnys_next)

    print("table_exists=True")
    print(f"XKRX calendar range={xkrx_min}..{xkrx_max}")
    print(f"XKRX today is_open={xkrx_diag.get('is_open')}")
    print(f"XKRX today source={xkrx_diag.get('source')}")
    print(f"XKRX today reason={xkrx_diag.get('reason')}")
    print(f"XKRX previous trading day={xkrx_previous}")
    print(f"XKRX next trading day={xkrx_next_day}")
    print(f"XNYS calendar range={xnys_min}..{xnys_max}")
    print(f"XNYS today is_open={xnys_diag.get('is_open')}")
    print(f"XNYS today source={xnys_diag.get('source')}")
    print(f"XNYS today reason={xnys_diag.get('reason')}")
    print(f"XNYS previous trading day={xnys_previous}")
    print(f"XNYS next trading day={xnys_next_day}")
    print(f"current year XKRX rows={len(xkrx_current)}")
    print(f"next year XKRX rows={len(xkrx_next)}")
    print(f"current year XNYS rows={len(xnys_current)}")
    print(f"next year XNYS rows={len(xnys_next)}")
    print(f"recent_30d_open_day_count={xkrx_recent_open_count}")
    print(f"next_30d_open_day_count={xkrx_next_open_count}")
    print(f"XKRX weekday_holiday_count={xkrx_weekday_holidays}")
    print(f"XNYS weekday_holiday_count={xnys_weekday_holidays}")
    print(f"manual_override_row_count={len(override_rows)}")
    print(
        "manual_override_rows_recent="
        f"{[{ 'exchange_code': row.get('exchange_code'), 'calendar_date': str(row.get('calendar_date'))[:10], 'is_open': row.get('is_open'), 'reason': row.get('reason')} for row in recent_override_rows]}"
    )
    specific_20260506 = next(
        (
            row for row in xkrx_current + xkrx_next
            if row.get("exchange_code") == "XKRX" and str(row.get("calendar_date"))[:10] == "2026-05-06"
        ),
        None,
    )
    print(f"XKRX 2026-05-06 override row={specific_20260506}")

    if len(xkrx_current) < 300:
        print("status=WARN_MARKET_CALENDAR_INCOMPLETE")
    elif len(xkrx_next) < 300:
        print("status=WARN_NEXT_YEAR_CALENDAR_INCOMPLETE")
    elif len(xnys_current) < 300:
        print("status=WARN_MARKET_CALENDAR_MISSING_XNYS")
    elif len(xnys_next) < 300:
        print("status=WARN_NEXT_YEAR_CALENDAR_INCOMPLETE_XNYS")
    elif not xkrx_today_rows:
        print("status=WARN_TODAY_CALENDAR_MISSING")
    elif xkrx_next_day is None:
        print("status=WARN_NEXT_TRADING_DAY_MISSING")
    else:
        print("status=SUCCESS")


def _print_price_coverage_by_market(loader: SupabaseLoader, today: date):
    print("\n=== PRICE COVERAGE BY MARKET ===")
    latest_xkrx_trading_day = get_latest_trading_day_on_or_before(loader, today, "XKRX")
    target_date = today.isoformat()
    print(f"latest_xkrx_trading_day={latest_xkrx_trading_day}")
    print(f"target_date={target_date}")
    if not latest_xkrx_trading_day:
        print("status=WARN_NO_XKRX_TRADING_DAY")
        return

    latest_day_str = latest_xkrx_trading_day.isoformat()
    rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest_day_str, latest_day_str)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    master_map = {
        normalize_symbol_value(row.get("symbol")): _standardize_market_value(row.get("market"))
        for row in master_rows
        if row.get("symbol")
    }
    summary = {
        "KOSPI": {"total": 0, "valid": 0, "zero": 0},
        "KOSDAQ": {"total": 0, "valid": 0, "zero": 0},
        "ETF": {"total": 0, "valid": 0, "zero": 0},
        "ETN": {"total": 0, "valid": 0, "zero": 0},
    }
    for row in rows:
        market = master_map.get(normalize_symbol_value(row.get("symbol")))
        if market not in summary:
            continue
        summary[market]["total"] += 1
        if is_valid_price_row(row, market_is_open=True):
            summary[market]["valid"] += 1
        elif row.get("close_price") not in (None, "") and (
            float(row.get("volume") or 0) <= 0 or float(row.get("trading_value") or 0) <= 0
        ):
            summary[market]["zero"] += 1

    for market in ("KOSPI", "KOSDAQ", "ETF", "ETN"):
        stats = summary[market]
        print(f"{market} total/valid/zero rows={stats['total']}/{stats['valid']}/{stats['zero']}")

    kr_valid_rows = summary["KOSPI"]["valid"] + summary["KOSDAQ"]["valid"]
    print(f"KOSPI+KOSDAQ valid rows={kr_valid_rows}")
    xkrx_is_open = is_market_open(loader, latest_xkrx_trading_day, "XKRX")
    if xkrx_is_open and kr_valid_rows < 100:
        print("status=FAIL_INSUFFICIENT_STOCK_PRICE_ROWS")
    elif xkrx_is_open and kr_valid_rows < 2000:
        print("status=WARN_PARTIAL_STOCK_PRICE_COVERAGE")
    elif any(summary[market]["zero"] > 0 for market in summary):
        print("status=WARN_OPEN_MARKET_ZERO_VOLUME_ROWS")
    else:
        print("status=SUCCESS")


def _print_ranking_source_date_quality(loader: SupabaseLoader, today: date):
    print("\n=== RANKING SOURCE DATE QUALITY ===")
    latest = _latest(loader, "normalized_market_rankings_daily", "base_date")
    if not latest:
        print("status=FAIL_RANKING_EMPTY")
        return

    normalized_rows = loader.fetch_all("normalized_market_rankings_daily", "base_date", latest, latest)
    raw_rows = loader.fetch_all("raw_market_rankings", "base_date", latest, latest)
    raw_map = {
        (
            row.get("base_date"),
            row.get("market"),
            row.get("rank_type"),
            normalize_symbol_value(row.get("symbol")),
        ): row
        for row in raw_rows
    }

    stale_kr_rows = 0
    missing_source_date_rows = 0
    etp_fallback_rows = 0
    samples = []
    for row in normalized_rows:
        market = _standardize_market_value(row.get("market"))
        symbol = normalize_symbol_value(row.get("symbol"))
        source_base_date = str(row.get("source_base_date"))[:10] if row.get("source_base_date") else None
        raw_row = raw_map.get((row.get("base_date"), row.get("market"), row.get("rank_type"), symbol))
        raw_payload = {}
        if raw_row and raw_row.get("raw_data"):
            try:
                raw_payload = json.loads(raw_row["raw_data"]) if isinstance(raw_row["raw_data"], str) else raw_row["raw_data"]
            except Exception:
                raw_payload = {}
        raw_price_base_date = raw_payload.get("price_base_date")
        effective_source_base_date = source_base_date or raw_price_base_date
        if market in {"KOSPI", "KOSDAQ"}:
            if row.get("source") == "VALID_PRICE_FALLBACK" and not effective_source_base_date:
                missing_source_date_rows += 1
            elif effective_source_base_date and str(row.get("base_date"))[:10] != str(effective_source_base_date)[:10]:
                stale_kr_rows += 1
                if len(samples) < 10:
                    samples.append(
                        {
                            "market": market,
                            "rank_type": row.get("rank_type"),
                            "symbol": symbol,
                            "base_date": row.get("base_date"),
                            "source_base_date": effective_source_base_date,
                            "source": row.get("source"),
                        }
                    )
        elif market in {"ETF", "ETN"} and effective_source_base_date and str(row.get("base_date"))[:10] != str(effective_source_base_date)[:10]:
            etp_fallback_rows += 1

    print(f"latest ranking date={latest}")
    print(f"kr_stale_ranking_rows={stale_kr_rows}")
    print(f"kr_missing_source_date_rows={missing_source_date_rows}")
    print(f"etf_etn_fallback_rows={etp_fallback_rows}")
    print(f"sample_stale_rows={samples}")
    if missing_source_date_rows > 0:
        print("status=FAIL_RANKING_SOURCE_DATE_MISSING")
    elif stale_kr_rows > 0:
        print("status=WARN_STALE_KR_STOCK_RANKING")
    else:
        print("status=SUCCESS")


def _print_report_readiness(loader: SupabaseLoader, today: date):
    print("\n=== REPORT READINESS ===")
    latest_xkrx_trading_day = get_latest_trading_day_on_or_before(loader, today, "XKRX")
    if not latest_xkrx_trading_day:
        print("status=WARN_NO_XKRX_TRADING_DAY")
        return
    latest_day_str = latest_xkrx_trading_day.isoformat()
    rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest_day_str, latest_day_str)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    master_map = {
        normalize_symbol_value(row.get("symbol")): (_standardize_market_value(row.get("market")), row.get("asset_type"))
        for row in master_rows
        if row.get("symbol")
    }
    kr_valid_rows = 0
    for row in rows:
        master = master_map.get(normalize_symbol_value(row.get("symbol")))
        if not master:
            continue
        market, asset_type = master
        if market in {"KOSPI", "KOSDAQ"} and asset_type == "STOCK" and is_valid_price_row(row, market_is_open=True):
            kr_valid_rows += 1
    print(f"latest_xkrx_trading_day={latest_day_str}")
    print(f"kr_stock_valid_price_rows={kr_valid_rows}")
    if is_market_open(loader, latest_xkrx_trading_day, "XKRX") and kr_valid_rows < 100:
        print("status=FAIL_KR_STOCK_PRICE_COVERAGE")
        print("note=Do not use same-day KOSPI/KOSDAQ Top rankings for report generation.")
    else:
        print("status=SUCCESS")


def _print_market_closed_ingestion_guardrail(loader: SupabaseLoader, today: date):
    print("\n=== MARKET CLOSED INGESTION GUARDRAIL ===")
    try:
        calendar_rows = loader.fetch_all(
            "market_trading_calendar",
            "calendar_date",
            (today - timedelta(days=30)).isoformat(),
            today.isoformat(),
        )
    except Exception as exc:
        print(f"status=WARN_MARKET_CALENDAR_MISSING note={exc}")
        return

    xkrx_closed_dates = [
        str(row.get("calendar_date"))[:10]
        for row in calendar_rows
        if row.get("exchange_code") == "XKRX" and not row.get("is_open")
    ]
    xnys_closed_dates = [
        str(row.get("calendar_date"))[:10]
        for row in calendar_rows
        if row.get("exchange_code") == "XNYS" and not row.get("is_open")
    ]

    krx_closed_row_count = 0
    if xkrx_closed_dates:
        ranking_rows = (
            loader.client.table("normalized_market_rankings_daily")
            .select("base_date")
            .in_("base_date", xkrx_closed_dates)
            .execute()
            .data
            or []
        )
        krx_closed_row_count = len(ranking_rows)

    us_closed_suspicious_rows = 0
    if xnys_closed_dates:
        macro_rows = (
            loader.client.table("normalized_global_macro_daily")
            .select("base_date, sp500, nasdaq, sox, vix")
            .in_("base_date", xnys_closed_dates)
            .execute()
            .data
            or []
        )
        us_closed_suspicious_rows = sum(
            1
            for row in macro_rows
            if any(row.get(field) is not None for field in ("sp500", "nasdaq", "sox", "vix"))
        )

    print(f"recent_xkrx_closed_dates_checked={len(xkrx_closed_dates)}")
    print(f"recent_xnys_closed_dates_checked={len(xnys_closed_dates)}")
    print(f"krx_closed_date_ranking_rows={krx_closed_row_count}")
    print(f"us_closed_date_equity_indicator_rows={us_closed_suspicious_rows}")

    if not any(row.get("exchange_code") == "XKRX" for row in calendar_rows):
        print("status=WARN_MARKET_CALENDAR_MISSING_XKRX")
    elif not any(row.get("exchange_code") == "XNYS" for row in calendar_rows):
        print("status=WARN_MARKET_CALENDAR_MISSING_XNYS")
    elif krx_closed_row_count > 0:
        print("status=WARN_KRX_CLOSED_DATE_MARKET_ROWS")
    elif us_closed_suspicious_rows > 0:
        print("status=WARN_US_CLOSED_DATE_MARKET_ROWS")
    else:
        print("status=SUCCESS")


def _print_open_market_zero_volume_quality(loader: SupabaseLoader, today: date):
    print("\n=== OPEN MARKET ZERO VOLUME PRICE ROWS ===")
    latest_xkrx_day = get_latest_trading_day_on_or_before(loader, today, "XKRX")
    if not latest_xkrx_day:
        print("status=WARN_NO_XKRX_TRADING_DAY")
        return

    latest_day_str = latest_xkrx_day.isoformat()
    price_rows = loader.fetch_all("normalized_stock_prices_daily", "base_date", latest_day_str, latest_day_str)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    market_map = {row.get("symbol"): row.get("market") for row in master_rows if row.get("symbol")}
    bad_rows = [
        row for row in price_rows
        if market_map.get(row.get("symbol")) in {"KOSPI", "KOSDAQ", "ETF", "ETN"}
        and row.get("close_price") not in (None, "")
        and (float(row.get("volume") or 0) <= 0 or float(row.get("trading_value") or 0) <= 0)
    ]
    print(f"latest_xkrx_open_date={latest_day_str}")
    print(f"open_market_zero_volume_or_value_rows={len(bad_rows)}")
    print(f"sample_bad_rows={bad_rows[:10]}")
    if bad_rows:
        print("status=WARN_OPEN_MARKET_ZERO_VOLUME_ROWS")
    else:
        print("status=SUCCESS")


def _print_feature_without_valid_price(loader: SupabaseLoader, today: date):
    print("\n=== FEATURE WITHOUT VALID PRICE ===")
    target_date = today.isoformat()
    feature_rows = loader.fetch_all("feature_store_daily", "base_date", target_date, target_date)
    valid_price_rows = [
        row for row in loader.fetch_all("normalized_stock_prices_daily", "base_date", target_date, target_date)
        if is_valid_price_row(row, market_is_open=True)
    ]
    print(f"target_date={target_date}")
    print(f"feature_row_count={len(feature_rows)}")
    print(f"valid_price_row_count={len(valid_price_rows)}")
    if feature_rows and len(valid_price_rows) < 100:
        print("status=WARN_FEATURE_WITHOUT_VALID_PRICE")
    else:
        print("status=SUCCESS")


def _print_stock_detail_universe_quality(config: dict, loader: SupabaseLoader):
    print("\n=== STOCK DETAIL UNIVERSE QUALITY ===")
    static_count = 0
    ranking_count = 0
    active_master_count = 0
    detail_universe_count = 0
    try:
        static_rows = (
            loader.client.table("static_stock_universe")
            .select("symbol", count="exact")
            .eq("enabled", True)
            .limit(1)
            .execute()
        )
        static_count = int(static_rows.count or 0)
    except Exception as exc:
        print(f"static enabled count=ERROR note={exc}")
    try:
        latest = _latest(loader, "normalized_market_rankings_daily", "base_date")
        if latest:
            ranking_rows = (
                loader.client.table("normalized_market_rankings_daily")
                .select("symbol")
                .eq("base_date", latest)
                .execute()
                .data
                or []
            )
            ranking_count = len({normalize_symbol_value(row.get("symbol")) for row in ranking_rows if row.get("symbol")})
    except Exception as exc:
        print(f"latest ranking symbol count=ERROR note={exc}")
    try:
        active_rows = (
            loader.client.table("stocks_master")
            .select("symbol", count="exact")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        active_master_count = int(active_rows.count or 0)
    except Exception as exc:
        print(f"stocks_master active count=ERROR note={exc}")

    try:
        universe_loader = DynamicUniverseLoader(config, collector=None)
        universe = asyncio.run(universe_loader.get_combined_universe(auto_backfill=False))
        detail_universe_count = len(universe)
    except Exception as exc:
        print(f"expected detail universe count=ERROR note={exc}")
        universe = []

    print(f"static enabled count={static_count}")
    print(f"latest ranking symbol count={ranking_count}")
    print(f"stocks_master active count={active_master_count}")
    print(f"expected detail universe count={detail_universe_count}")

    if detail_universe_count > 500:
        print("status=FAIL_DETAIL_UNIVERSE_TOO_LARGE")
    elif active_master_count > 1000:
        print("status=WARN_MASTER_ACTIVE_TOO_BROAD")
    elif ranking_count == 0:
        print("status=WARN_RANKING_UNIVERSE_EMPTY")
    else:
        print("status=SUCCESS")


def _print_report_required_etf_coverage(loader: SupabaseLoader, today: date):
    print("\n=== REPORT REQUIRED ETF COVERAGE ===")
    required_symbols = _load_report_required_etf_symbols(loader)
    latest_xkrx_day = get_latest_trading_day_on_or_before(loader, today, "XKRX")
    print(f"report_required_etf_universe active ETF count={len(required_symbols)}")
    print(f"latest_xkrx_trading_day={latest_xkrx_day}")
    if not required_symbols or not latest_xkrx_day:
        print("latest price 존재 ETF 수=0")
        print("missing ETF 목록=[]")
        print("stale ETF 목록=[]")
        print("status=WARN_ETF_COVERAGE_UNCONFIGURED")
        return

    latest_day_str = latest_xkrx_day.isoformat()
    rows = (
        loader.client.table("normalized_stock_prices_daily")
        .select("symbol, base_date, close_price, volume, trading_value")
        .eq("base_date", latest_day_str)
        .in_("symbol", required_symbols)
        .execute()
        .data
        or []
    )
    fresh_symbols = {
        normalize_symbol_value(row.get("symbol"))
        for row in rows
        if is_valid_price_row(row, market_is_open=True)
    }

    historical_rows = loader.fetch_all(
        "normalized_stock_prices_daily",
        "base_date",
        (latest_xkrx_day - timedelta(days=10)).isoformat(),
        latest_day_str,
    )
    latest_seen = {}
    for row in historical_rows:
        symbol = normalize_symbol_value(row.get("symbol"))
        if symbol not in required_symbols:
            continue
        if not is_valid_price_row(row, market_is_open=True):
            continue
        if symbol not in latest_seen or str(row.get("base_date")) > str(latest_seen[symbol].get("base_date")):
            latest_seen[symbol] = row

    missing_symbols = [symbol for symbol in required_symbols if symbol not in fresh_symbols and symbol not in latest_seen]
    stale_symbols = [
        symbol
        for symbol in required_symbols
        if symbol in latest_seen and str(latest_seen[symbol].get("base_date")) != latest_day_str
    ]

    print(f"최신 가격 존재 ETF 수={len(fresh_symbols)}")
    print(f"missing ETF 목록={missing_symbols[:20]}")
    print(f"stale ETF 목록={stale_symbols[:20]}")
    if missing_symbols:
        print("status=FAIL_REPORT_REQUIRED_ETF_MISSING")
    elif stale_symbols:
        print("status=WARN_REPORT_REQUIRED_ETF_STALE")
    else:
        print("status=SUCCESS")


def verify_data():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    runner_date_utc = get_current_utc().date()
    today = get_kst_target_date()
    target_date = today.isoformat()
    active = _active_symbols(loader)
    xkrx_is_open = is_market_open(loader, today, "XKRX")
    xnys_is_open = is_market_open(loader, today, "XNYS")

    print("\n=== DATA QUALITY SUMMARY ===\n")
    print(
        f"runner_date_utc={runner_date_utc} target_date_kst={target_date} "
        f"xkrx_is_open={xkrx_is_open} xnys_is_open={xnys_is_open}"
    )
    print(
        f"{'table_name':<38} | {'row_count':<9} | {'latest_date':<12} | "
        f"{'target_count':<12} | {'coverage':<10} | {'missing':<8} | {'stale':<5} | {'status':<18} | note"
    )
    print("-" * 150)

    for table, date_col in DATE_COLUMNS.items():
        try:
            row_count = _count(loader, table)
            latest = _latest(loader, table, date_col) if row_count else None
            target_count = _target_count(loader, table, date_col, target_date) if row_count else 0
            coverage, missing = _symbol_coverage(loader, table, latest or target_date, active)
            stale = _stale_days(latest, today)
            status, note = _status(table, row_count, target_count, stale, coverage, xkrx_is_open)
            coverage_text = f"{coverage:.1%}" if coverage is not None else "-"
            print(
                f"{table:<38} | {row_count:<9} | {latest or 'N/A':<12} | "
                f"{target_count:<12} | {coverage_text:<10} | {missing:<8} | {str(stale):<5} | {status:<18} | {note}"
            )
        except Exception as exc:
            print(f"{table:<38} | ERROR     | N/A          | 0            | -          | 0        | N/A   | FAIL_SCHEMA        | {exc}")

    for table in sorted(LEGACY_TABLES):
        try:
            row_count = _count(loader, table)
        except Exception:
            row_count = 0
        print(f"{table:<38} | {row_count:<9} | N/A          | 0            | -          | 0        | N/A   | LEGACY             | legacy table; not required")

    _print_price_quality(loader)
    _print_price_mapping_quality(loader)
    _print_ratio_and_short_quality(loader)
    _print_symbol_quality(loader)
    _print_market_ranking_quality(loader)
    _print_market_master_quality(loader)
    _print_price_coverage_by_market(loader, today)
    _print_ranking_source_quality(loader, today)
    _print_ranking_source_date_quality(loader, today)
    _print_report_readiness(loader, today)
    _print_macro_quality(loader, today)
    _print_market_calendar_quality(loader, today)
    _print_market_closed_ingestion_guardrail(loader, today)
    _print_open_market_zero_volume_quality(loader, today)
    _print_feature_without_valid_price(loader, today)
    _print_stock_detail_universe_quality(config, loader)
    _print_report_required_etf_coverage(loader, today)
    _macro_series_status(loader, today)


if __name__ == "__main__":
    verify_data()
