import argparse
import os
from datetime import date
from typing import Any

import pandas as pd

from src.features.signal_generator import SignalGenerator
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.universe_loader import load_universe

logger = get_logger(__name__)

REPORT_MIN_MARKET_PRICE_ROWS = 2000


def _page_table(loader: SupabaseLoader, table_name: str, select_expr: str, query_builder=None) -> list[dict]:
    rows: list[dict] = []
    chunk_size = 1000
    for page in range(0, 50):
        start = page * chunk_size
        end = start + chunk_size - 1
        query = loader.client.table(table_name).select(select_expr)
        if query_builder:
            query = query_builder(query)
        res = query.range(start, end).execute()
        data = res.data or []
        rows.extend(data)
        if len(data) < chunk_size:
            break
    return rows


def _latest_macro_date(loader: SupabaseLoader) -> str:
    res = (
        loader.client.table("normalized_global_macro_daily")
        .select("base_date")
        .order("base_date", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise RuntimeError("normalized_global_macro_daily has no rows; cannot choose report date")
    return res.data[0]["base_date"]


def validate_report_price_coverage(
    loader: SupabaseLoader,
    target_date: str,
    threshold: int = REPORT_MIN_MARKET_PRICE_ROWS,
) -> int:
    """Abort report generation when same-day KOSPI/KOSDAQ price coverage is incomplete."""
    master_rows = _page_table(
        loader,
        "stocks_master",
        "symbol,market",
        lambda q: q.in_("market", ["KOSPI", "KOSDAQ"]),
    )
    market_symbols = {row["symbol"] for row in master_rows if row.get("symbol")}
    if len(market_symbols) <= threshold:
        raise RuntimeError(
            f"stocks_master KOSPI/KOSDAQ universe is too small: "
            f"count={len(market_symbols)}, threshold={threshold}"
        )

    price_rows = loader.fetch_all(
        "normalized_stock_prices_daily",
        "base_date",
        target_date,
        target_date,
    )
    covered_symbols = {
        row["symbol"]
        for row in price_rows
        if row.get("symbol") in market_symbols and row.get("close_price") is not None
    }
    coverage_count = len(covered_symbols)
    if coverage_count <= threshold:
        raise RuntimeError(
            f"Report guardrail failed for {target_date}: "
            f"KOSPI/KOSDAQ price rows={coverage_count}, threshold={threshold}"
        )

    logger.info(
        f"Report guardrail passed for {target_date}: "
        f"KOSPI/KOSDAQ price rows={coverage_count}, threshold={threshold}"
    )
    return coverage_count


def _features_for_date(loader: SupabaseLoader, target_date: str) -> pd.DataFrame:
    rows = loader.fetch_all(
        "feature_store_daily",
        "base_date",
        target_date,
        target_date,
    )
    return pd.DataFrame(rows)


def _macro_snapshot(loader: SupabaseLoader, target_date: str) -> dict[str, Any]:
    res = (
        loader.client.table("normalized_global_macro_daily")
        .select("*")
        .eq("base_date", target_date)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else {}


def generate_report(target_date: str | None = None, threshold: int = REPORT_MIN_MARKET_PRICE_ROWS) -> str:
    logger.info("Starting daily stock report generation")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])

    report_date = target_date or _latest_macro_date(loader)
    coverage_count = validate_report_price_coverage(loader, report_date, threshold=threshold)

    universe = load_universe()
    symbol_to_name = {row["symbol"]: row.get("name", row["symbol"]) for row in universe}
    features_df = _features_for_date(loader, report_date)
    if features_df.empty:
        raise RuntimeError(f"feature_store_daily has no rows for {report_date}")

    signal_generator = SignalGenerator()
    signals = []
    for symbol, group in features_df.groupby("symbol"):
        feature_map = dict(zip(group["feature_name"], group["feature_value"]))
        signal = signal_generator.generate_signal(feature_map)
        signals.append(
            {
                "symbol": symbol,
                "name": symbol_to_name.get(symbol, symbol),
                "total_score": signal["total_score"],
                "signal": signal["signal"],
                "features": feature_map,
            }
        )

    results_df = pd.DataFrame(signals)
    if results_df.empty:
        raise RuntimeError(f"No report signals were generated for {report_date}")

    buy_count = int((results_df["signal"] == "BUY").sum())
    sell_count = int((results_df["signal"] == "SELL").sum())
    hold_count = int((results_df["signal"] == "HOLD").sum())
    top_rows = results_df.sort_values("total_score", ascending=False).head(10)
    macro = _macro_snapshot(loader, report_date)

    lines = [
        f"# Daily Stock Report ({report_date})",
        "",
        "## Data Coverage",
        f"- KOSPI/KOSDAQ price coverage: {coverage_count:,} symbols",
        f"- Feature rows: {len(features_df):,}",
        "",
        "## Market Snapshot",
        f"- KOSPI: {macro.get('kospi', 'N/A')} ({macro.get('kospi_change_rate', 'N/A')}%)",
        f"- KOSDAQ: {macro.get('kosdaq', 'N/A')} ({macro.get('kosdaq_change_rate', 'N/A')}%)",
        f"- USDKRW: {macro.get('usdkrw', 'N/A')}",
        f"- KR10Y: {macro.get('kr10y', 'N/A')}",
        "",
        "## Signal Summary",
        f"- BUY: {buy_count:,}",
        f"- HOLD: {hold_count:,}",
        f"- SELL: {sell_count:,}",
        "",
        "## Top Signals",
    ]

    for index, row in enumerate(top_rows.itertuples(index=False), 1):
        lines.append(
            f"{index}. {row.name} ({row.symbol}) - {row.signal}, score={row.total_score:.3f}"
        )

    report = "\n".join(lines)
    report_dir = "reports"
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"daily_report_{report_date}.md")
    with open(report_path, "w", encoding="utf-8") as report_file:
        report_file.write(report)

    logger.info(f"Daily stock report written to {os.path.abspath(report_path)}")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="Report date in YYYY-MM-DD or YYYYMMDD format")
    parser.add_argument("--threshold", type=int, default=REPORT_MIN_MARKET_PRICE_ROWS)
    args = parser.parse_args()

    target_date = args.date
    if target_date and len(target_date) == 8 and target_date.isdigit():
        target_date = date.fromisoformat(f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}").isoformat()

    try:
        generate_report(target_date=target_date, threshold=args.threshold)
        return 0
    except Exception as exc:
        logger.error(f"Report generation aborted: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
