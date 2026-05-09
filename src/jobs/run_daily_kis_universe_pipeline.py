from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.logger import get_logger
from src.utils.market_data_quality import is_valid_price_row
from src.utils.time_utils import generate_available_at_for_eod, get_current_utc, get_kst_target_date, parse_date_string
from src.utils.trading_calendar import should_skip_market_job

logger = get_logger(__name__)

DEFAULT_LIMIT = 300
HARD_LIMIT = 500


def _reset_target_date_kis_detail_rows(loader: SupabaseLoader, base_date: str) -> None:
    for table_name in (
        "raw_stock_prices_daily",
        "normalized_stock_prices_daily",
        "normalized_stock_snapshots_daily",
        "normalized_stock_fundamentals_ratios",
    ):
        try:
            (
                loader.client.table(table_name)
                .delete()
                .eq("base_date", base_date)
                .eq("source", "KIS_DETAIL")
                .execute()
            )
            logger.info(f"Cleared existing KIS_DETAIL rows for {table_name} base_date={base_date}")
        except Exception as exc:
            logger.warning(f"Failed to clear KIS_DETAIL rows for {table_name} base_date={base_date}: {exc}")


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
        "source": snapshot.get("source", "KIS_DETAIL"),
        "available_at": available_at,
    }


def _ratio_has_values(record: dict) -> bool:
    return any(record.get(field) not in (None, "") for field in ("per", "pbr", "roe", "debt_ratio"))


async def _with_retry(coro_factory, retries: int = 2):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # pragma: no cover - network/runtime path
            last_exc = exc
            if attempt >= retries:
                raise
            await asyncio.sleep(0.4 * (attempt + 1))
    if last_exc:
        raise last_exc


async def run_pipeline(target_date: date, limit: int | None = None, dry_run: bool = False) -> dict:
    requested_limit = min(limit or DEFAULT_LIMIT, HARD_LIMIT)
    logger.info(
        f"Starting KIS universe detail pipeline for {target_date:%Y-%m-%d} "
        f"(runner_date_utc={get_current_utc().date()}, target_date_kst={target_date}, dry_run={dry_run}, limit={requested_limit})"
    )
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    skip, reason = should_skip_market_job(loader, target_date, "XKRX", "daily_kis_universe_pipeline")
    if skip:
        message = f"XKRX market closed on {target_date:%Y-%m-%d}; skipped KIS universe detail ingestion. reason={reason}"
        logger.warning(message)
        if not dry_run:
            loader.insert_log("daily_kis_universe_pipeline", target_date.isoformat(), "SKIPPED_MARKET_CLOSED", 0, message)
        return {
            "status": "SKIPPED_MARKET_CLOSED",
            "universe_count": 0,
            "kis_detail_success_count": 0,
            "kis_price_valid_count": 0,
            "kis_snapshot_success_count": 0,
            "kis_ratio_success_count": 0,
            "failed_symbols_count": 0,
            "failed_symbols": [],
            "elapsed_seconds": 0.0,
        }

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    semaphore = asyncio.Semaphore(2)
    collector = KISDomesticStockCollector(config, auth_mgr, semaphore, write_enabled=not dry_run)
    universe_loader = DynamicUniverseLoader(config, collector)
    available_at = generate_available_at_for_eod(target_date).isoformat()
    base_date = target_date.isoformat()
    started_at = time.perf_counter()

    try:
        universe = await universe_loader.get_kis_detail_universe(requested_limit=requested_limit, include_live_kis_volume=True)
        if not universe:
            message = "No symbols available for KIS detail universe."
            logger.error(message)
            if not dry_run:
                loader.insert_log("daily_kis_universe_pipeline", base_date, "FAIL_KIS_UNIVERSE_EMPTY", 0, message)
            return {
                "status": "FAIL_KIS_UNIVERSE_EMPTY",
                "universe_count": 0,
                "kis_detail_success_count": 0,
                "kis_price_valid_count": 0,
                "kis_snapshot_success_count": 0,
                "kis_ratio_success_count": 0,
                "failed_symbols_count": 0,
                "failed_symbols": [],
                "elapsed_seconds": 0.0,
            }

        if not dry_run:
            _reset_target_date_kis_detail_rows(loader, base_date)

        price_valid_count = 0
        snapshot_success_count = 0
        ratio_success_count = 0
        success_count = 0
        failed_symbols: list[str] = []

        for idx, stock in enumerate(universe, 1):
            symbol = stock["symbol"]
            name = stock.get("name") or symbol
            try:
                price_row = await _with_retry(
                    lambda: collector.fetch_kis_daily_price(
                        symbol,
                        target_date=target_date,
                        available_at=available_at,
                        source_label="KIS_DETAIL",
                    )
                )
                snapshot = await _with_retry(
                    lambda: collector.fetch_kis_price_snapshot(
                        symbol,
                        base_date=base_date,
                        available_at=available_at,
                        source_label="KIS_DETAIL",
                    )
                )
                ratios = await _with_retry(
                    lambda: collector.fetch_kis_investment_ratios(
                        symbol,
                        base_date=base_date,
                        available_at=available_at,
                        source_label="KIS_DETAIL",
                    )
                )

                if price_row:
                    success_count += 1
                if price_row and is_valid_price_row(price_row, market_is_open=True):
                    price_valid_count += 1
                if snapshot:
                    snapshot_success_count += 1
                    if not dry_run:
                        loader.upsert_records("normalized_stock_snapshots_daily", [_to_snapshot_record(snapshot, available_at)])
                if ratios and _ratio_has_values(ratios):
                    ratio_success_count += 1
                    if not dry_run:
                        loader.upsert_records("normalized_stock_fundamentals_ratios", [ratios])
            except Exception as exc:  # pragma: no cover - network/runtime path
                failed_symbols.append(symbol)
                logger.warning(f"KIS detail ingestion failed for {name} ({symbol}): {exc}")

            if idx % 25 == 0 or idx == len(universe):
                logger.info(
                    f"KIS detail progress {idx}/{len(universe)} "
                    f"success={success_count} price_valid={price_valid_count} snapshot={snapshot_success_count} "
                    f"ratios={ratio_success_count} failed={len(failed_symbols)}"
                )
            await asyncio.sleep(0.15)

        status = "SUCCESS"
        if failed_symbols and success_count == 0:
            status = "FAIL_KIS_UNIVERSE_EMPTY"
        elif failed_symbols or price_valid_count < max(1, len(universe) // 3):
            status = "WARN_PARTIAL_KIS_UNIVERSE_COVERAGE"

        elapsed_seconds = round(time.perf_counter() - started_at, 2)
        error_message = (
            f"universe_count={len(universe)}; "
            f"failed_symbols_count={len(failed_symbols)}; "
            f"failed_symbols_sample={failed_symbols[:10]}"
        )
        if not dry_run:
            loader.insert_log(
                "daily_kis_universe_pipeline",
                base_date,
                status,
                success_count,
                error_message,
            )
        return {
            "status": status,
            "universe_count": len(universe),
            "kis_detail_success_count": success_count,
            "kis_price_valid_count": price_valid_count,
            "kis_snapshot_success_count": snapshot_success_count,
            "kis_ratio_success_count": ratio_success_count,
            "failed_symbols_count": len(failed_symbols),
            "failed_symbols": failed_symbols,
            "elapsed_seconds": elapsed_seconds,
        }
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
        target_dt = parse_date_string(args.date)

    result = asyncio.run(run_pipeline(target_dt, limit=args.limit, dry_run=args.dry_run))
    logger.info(f"KIS universe detail result={result}")
