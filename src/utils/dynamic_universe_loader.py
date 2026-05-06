import asyncio
import json
import os
from datetime import timedelta
from typing import Any, Dict, List

from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.krx_collector import KRXCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import get_current_kst

logger = get_logger(__name__)
DEFAULT_UNIVERSE_LIMIT = 300
HARD_UNIVERSE_LIMIT = 500


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


class DynamicUniverseLoader:
    def __init__(self, config: Dict[str, Any], collector: KISDomesticStockCollector):
        self.config = config
        self.collector = collector
        self.static_universe_path = "config/stock_universe.json"
        self.loader = SupabaseLoader(
            url=config.get("supabase", {}).get("url", ""),
            key=config.get("supabase", {}).get("service_role_key", ""),
        )

    def _load_static_universe(self) -> List[Dict[str, str]]:
        try:
            rows = (
                self.loader.client.table("static_stock_universe")
                .select("symbol, name, market")
                .eq("enabled", True)
                .execute()
                .data
                or []
            )
            if rows:
                return [
                    {
                        "code": normalize_symbol_value(row["symbol"]),
                        "name": row["name"],
                        "market": _standardize_market(row.get("market")),
                        "source_category": "static",
                    }
                    for row in rows
                    if row.get("symbol") and row.get("name")
                ]
        except Exception as exc:
            logger.warning(f"Failed to load static universe from DB, fallback to file: {exc}")

        if not os.path.exists(self.static_universe_path):
            return []

        with open(self.static_universe_path, "r", encoding="utf-8") as file:
            items = json.load(file)
        return [
            {
                "code": normalize_symbol_value(item["symbol"]),
                "name": item["name"],
                "market": _standardize_market(item.get("market")),
                "source_category": "manual",
            }
            for item in items
            if item.get("enabled", True) and item.get("symbol") and item.get("name")
        ]

    def _load_master_symbol_map(self) -> Dict[str, Dict[str, Any]]:
        try:
            rows = self.loader.client.table("stocks_master").select("symbol, name, market, asset_type, is_active").execute().data or []
        except Exception as exc:
            logger.warning(f"Failed to load stocks_master: {exc}")
            return {}
        symbol_map = {}
        for row in rows:
            symbol = normalize_symbol_value(row.get("symbol"))
            if not symbol:
                continue
            copied = dict(row)
            copied["symbol"] = symbol
            copied["market"] = _standardize_market(copied.get("market"))
            symbol_map[symbol] = copied
        return symbol_map

    @staticmethod
    def _parse_number(value):
        if value in (None, "", "-"):
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def _build_fallback_rankings_from_valid_prices(
        self,
        latest_date: str,
        valid_rows: List[Dict[str, Any]],
        master_symbol_map: Dict[str, Dict[str, Any]],
        market: str,
        rank_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        metric_key = rank_type
        if metric_key not in {"volume", "trading_value", "market_cap"}:
            return []
        filtered = []
        for row in valid_rows:
            symbol = normalize_symbol_value(row.get("symbol"))
            master_row = master_symbol_map.get(symbol)
            if not master_row or _standardize_market(master_row.get("market")) != market:
                continue
            metric_value = row.get(metric_key)
            if metric_value in (None, ""):
                continue
            filtered.append(
                {
                    "symbol": symbol,
                    "name": master_row.get("name") or symbol,
                    "market": market,
                    "volume": row.get("volume"),
                    "trading_value": row.get("trading_value"),
                    "market_cap": row.get("market_cap"),
                    "metric_value": metric_value,
                    "change_rate": None,
                    "base_date": latest_date,
                }
            )
        filtered.sort(key=lambda item: (item.get("metric_value") or 0), reverse=True)
        return filtered[:limit]

    def _persist_rankings(
        self,
        rows: List[Dict[str, Any]],
        market: str,
        rank_type: str,
        limit: int,
        source: str = "KIS",
        master_symbol_map: Dict[str, Dict[str, Any]] | None = None,
    ) -> List[Dict[str, str]]:
        base_date = get_current_kst().date().isoformat()
        available_at = get_current_kst().isoformat()
        master_symbol_map = master_symbol_map or self._load_master_symbol_map()
        allow_unverified_market = not master_symbol_map
        filtered_rows = []
        for row in rows or []:
            symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
            master_row = master_symbol_map.get(symbol)
            if not master_row and not allow_unverified_market:
                continue
            master_market = _standardize_market(master_row.get("market")) if master_row else market
            if not allow_unverified_market and market != "KOSPI200" and master_market != market:
                continue
            filtered_rows.append(
                {
                    "symbol": symbol,
                    "name": (master_row.get("name") if master_row else None) or row.get("hts_kor_isnm") or row.get("name") or symbol,
                    "volume": self._parse_number(row.get("acml_vol") or row.get("volume")),
                    "trading_value": self._parse_number(row.get("acml_tr_pbmn") or row.get("trading_value")),
                    "market_cap": self._parse_number(row.get("stck_avls") or row.get("market_cap")),
                    "change_rate": self._parse_number(row.get("prdy_ctrt") or row.get("change_rate")),
                    "raw_data": row,
                }
            )
        raw_records = []
        normalized_records = []
        universe_rows = []
        for rank, row in enumerate(filtered_rows[:limit], 1):
            metric_value = row["volume"] if rank_type == "volume" else row.get(rank_type)
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
                    "volume": row["volume"],
                    "trading_value": row["trading_value"],
                    "market_cap": row["market_cap"],
                    "change_rate": row["change_rate"],
                    "metric_value": metric_value,
                    "source": source,
                    "available_at": available_at,
                }
            )
            universe_rows.append({"code": row["symbol"], "name": row["name"], "market": market})
        if raw_records:
            self.loader.upsert_records("raw_market_rankings", raw_records)
        if normalized_records:
            self.loader.upsert_records("normalized_market_rankings_daily", normalized_records)
        return universe_rows

    def _load_latest_ranked_universe(self, master_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        try:
            latest_res = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("base_date")
                .order("base_date", desc=True)
                .limit(1)
                .execute()
            )
            if not latest_res.data:
                logger.warning("No ranking rows found; universe will fall back to static + active master only.")
                return []
            latest_date = latest_res.data[0]["base_date"]
            ranking_rows = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("symbol, name, market, rank_type")
                .eq("base_date", latest_date)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            logger.warning(f"Failed to load latest ranking rows: {exc}")
            return []

        ranked = []
        for row in ranking_rows:
            symbol = normalize_symbol_value(row.get("symbol"))
            master_row = master_map.get(symbol)
            if not master_row:
                continue
            master_market = _standardize_market(master_row.get("market"))
            ranking_market = _standardize_market(row.get("market"))
            if ranking_market != "KOSPI200" and ranking_market != master_market:
                continue
            ranked.append(
                {
                    "code": symbol,
                    "name": master_row.get("name") or row.get("name") or symbol,
                    "market": master_market or ranking_market,
                    "source_category": "ranking",
                }
            )
        return ranked

    async def _load_live_kis_volume_rank_universe(self, master_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        if not self.collector:
            return []
        try:
            ranking_rows = await self.collector.fetch_volume_rank(market_code="J")
        except Exception as exc:
            logger.warning(f"Failed to fetch live KIS volume ranking universe: {exc}")
            return []

        ranked: List[Dict[str, str]] = []
        for row in ranking_rows or []:
            symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
            master_row = master_map.get(symbol)
            if not symbol or not master_row:
                continue
            market = _standardize_market(master_row.get("market"))
            asset_type = master_row.get("asset_type")
            if market in {"KOSPI", "KOSDAQ"} and asset_type != "STOCK":
                continue
            if market == "ETF" and asset_type != "ETF":
                continue
            if market == "ETN" and asset_type != "ETN":
                continue
            ranked.append(
                {
                    "code": symbol,
                    "name": master_row.get("name") or row.get("hts_kor_isnm") or symbol,
                    "market": market,
                    "source_category": "kis_volume_rank",
                }
            )
        return ranked

    def _load_latest_kis_ranking_universe(self, master_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        try:
            latest_res = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("base_date")
                .eq("source", "KIS")
                .order("base_date", desc=True)
                .limit(1)
                .execute()
            )
            if not latest_res.data:
                return []
            latest_date = latest_res.data[0]["base_date"]
            ranking_rows = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("symbol, name, market, source")
                .eq("base_date", latest_date)
                .eq("source", "KIS")
                .execute()
                .data
                or []
            )
        except Exception as exc:
            logger.warning(f"Failed to load latest KIS ranking rows: {exc}")
            return []

        ranked: List[Dict[str, str]] = []
        for row in ranking_rows:
            symbol = normalize_symbol_value(row.get("symbol"))
            master_row = master_map.get(symbol)
            if not symbol or not master_row:
                continue
            ranked.append(
                {
                    "code": symbol,
                    "name": master_row.get("name") or row.get("name") or symbol,
                    "market": _standardize_market(master_row.get("market")) or _standardize_market(row.get("market")),
                    "source_category": "report_rank",
                }
            )
        return ranked

    @staticmethod
    def _prioritize_universe(symbol_rows: List[Dict[str, Any]], requested_limit: int | None) -> List[Dict[str, Any]]:
        max_limit = requested_limit or DEFAULT_UNIVERSE_LIMIT
        max_limit = min(max_limit, HARD_UNIVERSE_LIMIT)

        def _priority(stock: Dict[str, Any]) -> tuple[int, str]:
            parts = {part.strip() for part in str(stock.get("source_category") or "").split(",") if part.strip()}
            if "static" in parts:
                return (0, stock.get("symbol", ""))
            if "manual" in parts:
                return (1, stock.get("symbol", ""))
            if "watchlist" in parts:
                return (2, stock.get("symbol", ""))
            if "kis_volume_rank" in parts:
                return (3, stock.get("symbol", ""))
            if "report_rank" in parts or "ranking" in parts:
                return (4, stock.get("symbol", ""))
            return (5, stock.get("symbol", ""))

        if len(symbol_rows) > HARD_UNIVERSE_LIMIT:
            logger.warning(
                f"KIS detail universe exceeded hard limit; truncating from {len(symbol_rows)} to {HARD_UNIVERSE_LIMIT}"
            )
        ordered = sorted(symbol_rows, key=_priority)
        return ordered[:max_limit]

    async def get_combined_universe(self, auto_backfill: bool = True) -> List[Dict[str, Any]]:
        del auto_backfill
        logger.info("Loading combined universe from static universe and ranking table.")
        master_map = self._load_master_symbol_map()
        static_rows = self._load_static_universe()
        ranked_rows = self._load_latest_ranked_universe(master_map)

        combined: Dict[str, Dict[str, Any]] = {}
        for category, rows in (
            ("static", static_rows),
            ("ranking", ranked_rows),
        ):
            for row in rows:
                symbol = normalize_symbol_value(row.get("code"))
                if not symbol:
                    continue
                source_category = row.get("source_category") or category
                existing = combined.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "name": row.get("name") or symbol,
                        "market": _standardize_market(row.get("market")),
                        "sources": set(),
                    },
                )
                existing["name"] = existing.get("name") or row.get("name") or symbol
                if not existing.get("market"):
                    existing["market"] = _standardize_market(row.get("market"))
                existing["sources"].add(source_category)

        final_universe = [
            {
                "symbol": symbol,
                "name": payload["name"],
                "market": payload["market"],
                "source_category": ",".join(sorted(payload["sources"])),
            }
            for symbol, payload in combined.items()
        ]
        logger.info(f"Combined universe loaded: {len(final_universe)} symbols")
        return final_universe

    async def get_kis_detail_universe(
        self,
        requested_limit: int | None = None,
        include_live_kis_volume: bool = True,
    ) -> List[Dict[str, Any]]:
        logger.info("Loading KIS detail universe from static/manual sources and KIS/report rankings.")
        master_map = self._load_master_symbol_map()
        static_rows = self._load_static_universe()
        report_rows = self._load_latest_kis_ranking_universe(master_map)
        legacy_ranked_rows = self._load_latest_ranked_universe(master_map)
        kis_volume_rows = await self._load_live_kis_volume_rank_universe(master_map) if include_live_kis_volume else []

        combined: Dict[str, Dict[str, Any]] = {}
        for category, rows in (
            ("static", static_rows),
            ("kis_volume_rank", kis_volume_rows),
            ("report_rank", report_rows),
            ("ranking", legacy_ranked_rows),
        ):
            for row in rows:
                symbol = normalize_symbol_value(row.get("code"))
                if not symbol:
                    continue
                source_category = row.get("source_category") or category
                existing = combined.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "name": row.get("name") or symbol,
                        "market": _standardize_market(row.get("market")),
                        "sources": set(),
                    },
                )
                existing["name"] = existing.get("name") or row.get("name") or symbol
                if not existing.get("market"):
                    existing["market"] = _standardize_market(row.get("market"))
                existing["sources"].add(source_category)

        final_universe = [
            {
                "symbol": symbol,
                "name": payload["name"],
                "market": payload["market"],
                "source_category": ",".join(sorted(payload["sources"])),
            }
            for symbol, payload in combined.items()
        ]
        prioritized = self._prioritize_universe(final_universe, requested_limit)
        logger.info(
            "KIS detail universe loaded: "
            f"combined={len(final_universe)}, final={len(prioritized)}, requested_limit={requested_limit or DEFAULT_UNIVERSE_LIMIT}"
        )
        return prioritized

    def fetch_full_universe(self, target_date=None) -> List[Dict[str, Any]]:
        min_count = int(self.config.get("pipeline", {}).get("full_universe_min_count", 2000))
        krx_collector = KRXCollector(auth_key=self.config.get("krx", {}).get("auth_key", ""))
        rows = krx_collector.fetch_full_universe(target_date=target_date)
        universe = [
            {
                "symbol": normalize_symbol_value(item["code"]),
                "name": item.get("name") or item["code"],
                "market": _standardize_market(item.get("market")),
                "source_category": "full_universe",
            }
            for item in rows
            if item.get("market") in {"KOSPI", "KOSDAQ"}
        ]
        if len(universe) < min_count:
            logger.warning(
                f"Full universe count is below guardrail: count={len(universe)}, min_count={min_count}"
            )
        return universe

    async def trigger_auto_backfill(self, symbols: List[str]):
        end_dt = get_current_kst()
        start_dt = end_dt - timedelta(days=90)
        start_date_str = start_dt.strftime("%Y%m%d")
        end_date_str = end_dt.strftime("%Y%m%d")

        for idx, symbol in enumerate(symbols):
            logger.info(f"[{idx+1}/{len(symbols)}] Auto backfill in progress - {symbol}")
            try:
                available_at = get_current_kst().isoformat()
                await self.collector.fetch_ohlcv(
                    symbol,
                    timeframe="D",
                    start_date=start_date_str,
                    end_date=end_date_str,
                    available_at=available_at,
                )
                await self.collector.fetch_investor_trend(symbol, available_at=available_at)
                await self.collector.fetch_fundamental_info(
                    symbol,
                    base_date=end_dt.strftime("%Y-%m-%d"),
                    available_at=available_at,
                )
            except Exception as exc:
                logger.error(f"Auto backfill failed - {symbol}: {exc}")
            await asyncio.sleep(0.5)
