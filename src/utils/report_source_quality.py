from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from statistics import median
from typing import Any

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.market_data_quality import is_valid_price_row
from src.utils.symbols import normalize_symbol_value
from src.utils.trading_calendar import get_trading_days_between

WATCHLIST_SYMBOLS = ("005930", "000660", "071050", "278470", "058470")
WATCHLIST_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "071050": "한국금융지주",
    "278470": "에이피알",
    "058470": "리노공업",
}
PRICE_SCALE_BOUNDS = {
    "005930": (50_000, 500_000),
    "000660": (200_000, 3_000_000),
    "071050": (50_000, 500_000),
    "278470": (100_000, 1_000_000),
    "058470": (50_000, 500_000),
}


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fetch_price_rows(loader: SupabaseLoader, symbol: str, target_date: date, lookback_days: int = 90) -> list[dict[str, Any]]:
    start_date = (target_date - timedelta(days=lookback_days)).isoformat()
    rows = (
        loader.client.table("normalized_stock_prices_daily")
        .select("*")
        .eq("symbol", normalize_symbol_value(symbol))
        .gte("base_date", start_date)
        .lte("base_date", target_date.isoformat())
        .order("base_date")
        .execute()
        .data
        or []
    )
    return rows


def _fetch_feature_rows(loader: SupabaseLoader, symbol: str, target_date: date) -> list[dict[str, Any]]:
    return (
        loader.client.table("feature_store_daily")
        .select("*")
        .eq("symbol", normalize_symbol_value(symbol))
        .eq("base_date", target_date.isoformat())
        .execute()
        .data
        or []
    )


def _pivot_feature_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(row.get("feature_name")): row.get("feature_value") for row in rows if row.get("feature_name")}


def _window_rows(valid_rows: list[dict[str, Any]], window_size: int) -> list[dict[str, Any]]:
    required = window_size + 1
    if len(valid_rows) < required:
        return []
    return valid_rows[-required:]


def _window_source_summary(valid_rows: list[dict[str, Any]], window_size: int) -> dict[str, Any]:
    rows = _window_rows(valid_rows, window_size)
    if not rows:
        return {"window_ready": False, "source_mixed": False, "sources": {}, "window_size": window_size}
    sources = [str(row.get("source") or "UNKNOWN") for row in rows]
    counter = Counter(sources)
    return {
        "window_ready": True,
        "source_mixed": len(counter) > 1,
        "sources": dict(counter),
        "window_size": window_size,
    }


def detect_price_scale_warning(symbol: str, valid_rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not valid_rows:
        return warnings
    latest_close = _safe_float(valid_rows[-1].get("close_price"))
    if latest_close is None:
        return warnings
    bounds = PRICE_SCALE_BOUNDS.get(symbol)
    if bounds and not (bounds[0] <= latest_close <= bounds[1]):
        warnings.append("WARN_PRICE_SCALE_ANOMALY")

    history = [_safe_float(row.get("close_price")) for row in valid_rows[-21:-1]]
    history = [value for value in history if value and value > 0]
    if len(history) >= 5:
        hist_median = median(history)
        if hist_median > 0:
            ratio = latest_close / hist_median
            if ratio >= 1.8 or ratio <= 0.55:
                warnings.append("WARN_PRICE_JUMP_ANOMALY")
    return sorted(set(warnings))


def trading_stale_days(loader: SupabaseLoader, latest_date: date | None, target_date: date, exchange_code: str = "XKRX") -> int | None:
    if latest_date is None:
        return None
    if latest_date > target_date:
        return -1
    trading_days = get_trading_days_between(loader, latest_date, target_date, exchange_code=exchange_code)
    if not trading_days:
        return 0
    return max(len(trading_days) - 1, 0)


def analyze_watchlist_symbol(loader: SupabaseLoader, symbol: str, target_date: date) -> dict[str, Any]:
    symbol = normalize_symbol_value(symbol)
    rows = _fetch_price_rows(loader, symbol, target_date)
    valid_rows = [row for row in rows if is_valid_price_row(row, market_is_open=True)]
    latest_row = valid_rows[-1] if valid_rows else None
    latest_date = _safe_date(latest_row.get("base_date")) if latest_row else None
    stale_days = trading_stale_days(loader, latest_date, target_date, exchange_code="XKRX")
    source_summary_5d = _window_source_summary(valid_rows, 5)
    source_summary_20d = _window_source_summary(valid_rows, 20)
    source_mixed = source_summary_5d["source_mixed"] or source_summary_20d["source_mixed"]
    price_scale_warning = detect_price_scale_warning(symbol, valid_rows)
    status = "SUCCESS"
    if stale_days is not None and stale_days > 3:
        status = "WARN_REPORT_SOURCE_STALE"
    if source_mixed:
        status = "WARN_FEATURE_SOURCE_MIXED"
    if price_scale_warning:
        status = "WARN_PRICE_SCALE_ANOMALY"
    return_5d = None
    if len(valid_rows) >= 6:
        latest_close = _safe_float(valid_rows[-1].get("close_price"))
        base_close_5d = _safe_float(valid_rows[-6].get("close_price"))
        if latest_close is not None and base_close_5d not in (None, 0):
            return_5d = (latest_close / base_close_5d) - 1

    return_20d = None
    if len(valid_rows) >= 21:
        latest_close = _safe_float(valid_rows[-1].get("close_price"))
        base_close_20d = _safe_float(valid_rows[-21].get("close_price"))
        if latest_close is not None and base_close_20d not in (None, 0):
            return_20d = (latest_close / base_close_20d) - 1

    return {
        "symbol": symbol,
        "name": WATCHLIST_NAMES.get(symbol),
        "latest_base_date": latest_date.isoformat() if latest_date else None,
        "source": latest_row.get("source") if latest_row else None,
        "close_price": _safe_float(latest_row.get("close_price")) if latest_row else None,
        "previous_price_source_summary": {
            "5d": source_summary_5d["sources"],
            "20d": source_summary_20d["sources"],
        },
        "return_5d": return_5d,
        "return_20d": return_20d,
        "source_mixed": source_mixed,
        "price_scale_warning": price_scale_warning,
        "stale_days": stale_days,
        "status": status,
        "valid_rows": valid_rows,
    }


def analyze_feature_source_quality(loader: SupabaseLoader, symbol: str, target_date: date) -> dict[str, Any]:
    watch = analyze_watchlist_symbol(loader, symbol, target_date)
    feature_map = _pivot_feature_rows(_fetch_feature_rows(loader, symbol, target_date))
    source_summary_5d = _window_source_summary(watch["valid_rows"], 5)
    source_summary_20d = _window_source_summary(watch["valid_rows"], 20)
    calculation_source_consistent = not (source_summary_5d["source_mixed"] or source_summary_20d["source_mixed"])
    status = "SUCCESS" if calculation_source_consistent else "WARN_FEATURE_SOURCE_MIXED"
    return {
        "symbol": symbol,
        "name": watch.get("name"),
        "return_5d": feature_map.get("return_5d"),
        "return_20d": feature_map.get("return_20d"),
        "trading_value_ratio_20d": feature_map.get("trading_value_ratio_20d"),
        "calculation_source_consistent": calculation_source_consistent,
        "source_mixed_warning": None if calculation_source_consistent else {
            "5d_sources": source_summary_5d["sources"],
            "20d_sources": source_summary_20d["sources"],
        },
        "status": status,
    }


def detect_macro_suspicious_values(row: dict[str, Any]) -> list[str]:
    suspicious = []
    bounds = {
        "sp500": (2000, 15000),
        "nasdaq": (5000, 40000),
        "sox": (1000, 20000),
        "brent": (20, 200),
        "wti": (20, 200),
        "us10y": (0, 10),
        "us3y": (0, 10),
    }
    for field, (low, high) in bounds.items():
        value = _safe_float(row.get(field))
        if value is None:
            continue
        if not (low <= value <= high):
            suspicious.append(field)
    return suspicious


def fetch_latest_macro_row(loader: SupabaseLoader, target_date: date) -> dict[str, Any] | None:
    rows = (
        loader.client.table("normalized_global_macro_daily")
        .select("*")
        .lte("base_date", target_date.isoformat())
        .order("base_date", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def analyze_report_views(loader: SupabaseLoader, target_date: date) -> dict[str, Any]:
    target_date_str = target_date.isoformat()
    watch_rows = (
        loader.client.table("report_watchlist_snapshot_view")
        .select("*")
        .execute()
        .data
        or []
    )
    etf_rows = (
        loader.client.table("report_sector_etf_signal_view")
        .select("*")
        .execute()
        .data
        or []
    )
    future_watch = [row for row in watch_rows if str(row.get("base_date") or "")[:10] > target_date_str]
    future_etf = [row for row in etf_rows if str(row.get("latest_price_date") or "")[:10] > target_date_str]
    stale_watch = [row for row in watch_rows if row.get("data_status") == "STALE"]
    stale_sector = [
        row for row in etf_rows
        if (row.get("stale_days") or 0) > 3 or str(row.get("data_status") or "").startswith("STALE")
    ]
    status = "SUCCESS"
    if future_watch or future_etf:
        status = "FAIL_REPORT_SOURCE_QUALITY"
    elif stale_watch or stale_sector:
        status = "WARN_REPORT_SOURCE_STALE"
    return {
        "report_watchlist_snapshot_view_rows": len(watch_rows),
        "report_sector_etf_signal_view_rows": len(etf_rows),
        "stale_watchlist_count": len(stale_watch),
        "stale_sector_etf_count": len(stale_sector),
        "future_date_rows": len(future_watch) + len(future_etf),
        "status": status,
    }
