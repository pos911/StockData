from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any
import json

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.time_utils import get_current_kst
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
        rows = [
            row
            for row in rows
            if row.get("close_price") not in (None, "")
            and row.get("volume") not in (None, "")
            and row.get("trading_value") not in (None, "")
        ]
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


def _status(table: str, row_count: int, target_count: int, stale: int | None, coverage: float | None) -> tuple[str, str]:
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
        if row.get("close_price") not in (None, "")
        and row.get("volume") not in (None, "")
        and row.get("trading_value") not in (None, "")
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
    ranking_rows = loader.fetch_all("normalized_market_rankings_daily", "base_date", latest, latest)
    master_rows = loader.fetch_all("stocks_master", "updated_at", "1900-01-01", "2999-12-31")
    master_map = {row["symbol"]: row for row in master_rows if row.get("symbol")}
    mismatch_rows = 0
    q_prefix_rows = 0
    kis_kospi_volume = 0
    kis_kosdaq_volume = 0
    trading_value_counts = {}
    market_cap_counts = {}
    legacy_kis_rankings = 0
    for row in ranking_rows:
        symbol = row.get("symbol")
        if is_q_prefixed_numeric_symbol(symbol):
            q_prefix_rows += 1
        master = master_map.get(symbol)
        ranking_market = _standardize_market_value(row.get("market"))
        master_market = _standardize_market_value(master.get("market")) if master else None
        if ranking_market != "KOSPI200" and master and master_market != ranking_market:
            mismatch_rows += 1
        if row.get("source") == "KIS" and row.get("rank_type") == "volume" and ranking_market == "KOSPI":
            kis_kospi_volume += 1
        if row.get("source") == "KIS" and row.get("rank_type") == "volume" and ranking_market == "KOSDAQ":
            kis_kosdaq_volume += 1
        if row.get("rank_type") == "trading_value":
            trading_value_counts[ranking_market] = trading_value_counts.get(ranking_market, 0) + 1
        if row.get("rank_type") == "market_cap":
            market_cap_counts[ranking_market] = market_cap_counts.get(ranking_market, 0) + 1
        if row.get("source") == "KIS" and row.get("rank_type") in ("trading_value", "market_cap"):
            legacy_kis_rankings += 1
    stale_days = _stale_days(latest, today)
    print(f"latest ranking date={latest}")
    print(f"KIS KOSPI volume count={kis_kospi_volume}")
    print(f"KIS KOSDAQ volume count={kis_kosdaq_volume}")
    print(f"trading_value ranking count by market={trading_value_counts}")
    print(f"market_cap ranking count by market={market_cap_counts}")
    print(f"market mismatch rows={mismatch_rows}")
    print(f"q_prefix rows={q_prefix_rows}")
    print(f"stale ranking rows={0 if stale_days is None else stale_days}")
    if kis_kospi_volume == 0:
        print("status=FAIL_KIS_KOSPI_VOLUME_RANK")
    elif kis_kosdaq_volume == 0:
        print("status=FAIL_KIS_KOSDAQ_VOLUME_RANK")
    elif mismatch_rows > 0:
        print("status=FAIL_RANKING_MARKET_MISMATCH")
    elif q_prefix_rows > 0:
        print("status=FAIL_SYMBOL_NORMALIZATION")
    elif legacy_kis_rankings > 0:
        print("status=WARN_LEGACY_KIS_RANKING")
    else:
        print("status=SUCCESS")


def verify_data():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    today = get_current_kst().date()
    target_date = today.isoformat()
    active = _active_symbols(loader)

    print("\n=== DATA QUALITY SUMMARY ===\n")
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
            status, note = _status(table, row_count, target_count, stale, coverage)
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
    _print_ranking_source_quality(loader, today)
    _macro_series_status(loader, today)


if __name__ == "__main__":
    verify_data()
