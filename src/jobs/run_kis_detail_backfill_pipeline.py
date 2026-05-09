from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date, timedelta
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.jobs.run_daily_kis_universe_pipeline import HARD_LIMIT
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.logger import get_logger
from src.utils.time_utils import generate_available_at_for_eod, get_current_utc, get_kst_target_date, parse_date_string
from src.utils.trading_calendar import get_trading_days_between, should_skip_market_job

logger = get_logger(__name__)
DEFAULT_LIMIT = 100
DEFAULT_LOOKBACK_DAYS = 60


async def _with_retry(coro_factory, retries: int = 1):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            if attempt >= retries:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    if last_exc:
        raise last_exc


async def run_pipeline(target_date: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS, limit: int = DEFAULT_LIMIT, dry_run: bool = False) -> dict:
    requested_limit = min(limit or DEFAULT_LIMIT, HARD_LIMIT)
    logger.info(
        f"Starting KIS detail backfill pipeline for {target_date:%Y-%m-%d} "
        f"(runner_date_utc={get_current_utc().date()}, lookback_days={lookback_days}, limit={requested_limit}, dry_run={dry_run})"
    )
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    skip, reason = should_skip_market_job(loader, target_date, "XKRX", "daily_kis_detail_backfill_pipeline")
    if skip:
        message = f"XKRX market closed on {target_date:%Y-%m-%d}; skipped KIS detail backfill. reason={reason}"
        logger.warning(message)
        if not dry_run:
            loader.insert_log("daily_kis_detail_backfill_pipeline", target_date.isoformat(), "SKIPPED_MARKET_CLOSED", 0, message)
        return {"status": "SKIPPED_MARKET_CLOSED", "universe_count": 0, "processed_symbols": 0, "elapsed_seconds": 0.0}

    end_calendar_start = target_date - timedelta(days=max(lookback_days * 2, 120))
    trading_days = get_trading_days_between(loader, end_calendar_start, target_date, "XKRX")
    if not trading_days:
        trading_days = [target_date]
    window = trading_days[-max(lookback_days, 1):]
    start_date = window[0]
    start_ymd = start_date.strftime("%Y%m%d")
    end_ymd = target_date.strftime("%Y%m%d")
    available_at = generate_available_at_for_eod(target_date).isoformat()
    started_at = time.perf_counter()

    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    semaphore = asyncio.Semaphore(2)
    collector = KISDomesticStockCollector(config, auth_mgr, semaphore, write_enabled=not dry_run)
    universe_loader = DynamicUniverseLoader(config, collector)

    try:
        universe = await universe_loader.get_kis_detail_universe(
            requested_limit=requested_limit,
            include_live_kis_volume=False,
        )
        processed_symbols = 0
        failed_symbols: list[str] = []

        for idx, stock in enumerate(universe, 1):
            symbol = stock["symbol"]
            try:
                await _with_retry(
                    lambda: collector.fetch_ohlcv(
                        symbol,
                        timeframe="D",
                        start_date=start_ymd,
                        end_date=end_ymd,
                        available_at=available_at,
                        source_label="KIS_DETAIL",
                    )
                )
                processed_symbols += 1
            except Exception as exc:  # pragma: no cover
                failed_symbols.append(symbol)
                logger.warning(f"KIS detail backfill failed for {symbol}: {exc}")

            if idx % 20 == 0 or idx == len(universe):
                logger.info(
                    f"KIS detail backfill progress {idx}/{len(universe)} "
                    f"processed={processed_symbols} failed={len(failed_symbols)}"
                )
            await asyncio.sleep(0.2)

        elapsed_seconds = round(time.perf_counter() - started_at, 2)
        status = "SUCCESS" if failed_symbols == [] else "WARN_PARTIAL_KIS_UNIVERSE_COVERAGE"
        message = (
            f"universe_count={len(universe)}; processed_symbols={processed_symbols}; "
            f"lookback_days={lookback_days}; start_date={start_date.isoformat()}; "
            f"failed_symbols_count={len(failed_symbols)}; failed_symbols_sample={failed_symbols[:10]}"
        )
        if not dry_run:
            loader.insert_log(
                "daily_kis_detail_backfill_pipeline",
                target_date.isoformat(),
                status,
                processed_symbols,
                message,
            )
        return {
            "status": status,
            "universe_count": len(universe),
            "processed_symbols": processed_symbols,
            "failed_symbols_count": len(failed_symbols),
            "failed_symbols": failed_symbols,
            "start_date": start_date.isoformat(),
            "elapsed_seconds": elapsed_seconds,
        }
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_dt = get_kst_target_date(get_current_utc())
    if args.date:
        target_dt = parse_date_string(args.date)

    result = asyncio.run(
        run_pipeline(
            target_dt,
            lookback_days=args.lookback_days,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )
    logger.info(f"KIS detail backfill result={result}")
