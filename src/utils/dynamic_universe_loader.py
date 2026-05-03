import json
import os
import asyncio
import re
from typing import List, Dict, Any, Set
from datetime import timedelta

from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.krx_collector import KRXCollector
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.symbols import normalize_symbol_value

logger = get_logger(__name__)
ETF_PREFIXES = ("KODEX", "TIGER", "ACE", "RISE", "SOL", "HANARO", "KOSEF", "TIMEFOLIO")


def _is_etf_like_name(name: str | None) -> bool:
    if not name:
        return False
    upper_name = str(name).strip().upper()
    if "ETN" in upper_name:
        return False
    if upper_name.startswith("PLUS "):
        return True
    return upper_name.startswith(ETF_PREFIXES) or " ETF" in upper_name or upper_name == "ETF"


def _infer_market_from_name(name: str | None) -> str | None:
    if not name:
        return None
    upper_name = str(name).strip().upper()
    if "ETN" in upper_name:
        return "ETN"
    if _is_etf_like_name(name):
        return "ETF"
    return None


def _standardize_market(market: str | None, name: str | None = None) -> str | None:
    inferred = _infer_market_from_name(name)
    if inferred:
        return inferred
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
    """
    실시간 API 연동형 '스마트 유니버스' 시스템.
    6가지 카테고리(커스텀, 거래량 상위, 시총 상위, 인덱스 구성 종목 등)에서 
    종목을 동적으로 수집하고 중복을 제거하여 최적화된 유니버스를 구성합니다.
    """
    def __init__(self, config: Dict[str, Any], collector: KISDomesticStockCollector):
        self.config = config
        self.collector = collector
        self.static_universe_path = "config/stock_universe.json"
        
        supabase_url = config.get("supabase", {}).get("url", "")
        supabase_key = config.get("supabase", {}).get("service_role_key", "")
        self.loader = SupabaseLoader(url=supabase_url, key=supabase_key)

    def _load_static_universe(self) -> List[Dict[str, str]]:
        try:
            res = (
                self.loader.client.table("static_stock_universe")
                .select("symbol, name, market")
                .eq("enabled", True)
                .execute()
            )
            if res.data:
                return [
                    {
                        "code": normalize_symbol_value(item["symbol"]),
                        "name": item["name"],
                        "market": _standardize_market(item.get("market"), item.get("name")),
                    }
                    for item in res.data
                    if item.get("symbol") and item.get("name")
                ]
        except Exception as exc:
            logger.warning(f"Failed to load static universe from DB, fallback to file: {exc}")

        if not os.path.exists(self.static_universe_path):
            logger.warning(f"Static universe file is missing: {self.static_universe_path}")
            return []

        try:
            with open(self.static_universe_path, "r", encoding="utf-8") as file:
                items = json.load(file)
        except Exception as exc:
            logger.error(f"Failed to read static universe file: {exc}")
            return []

        results = []
        for item in items:
            if not item.get("enabled", True):
                continue
            symbol = item.get("symbol")
            name = item.get("name")
            if symbol and name:
                results.append(
                    {
                        "code": normalize_symbol_value(symbol),
                        "name": name,
                        "market": _standardize_market(item.get("market"), name),
                    }
                )
        return results

    @staticmethod
    def _resolve_market(existing_market: str | None, new_market: str | None) -> str | None:
        existing_market = _standardize_market(existing_market)
        new_market = _standardize_market(new_market)
        if not existing_market:
            return new_market
        if existing_market == "DYNAMIC" and new_market:
            return new_market
        return existing_market


    @staticmethod
    def _merge_latest_price_record(price_records: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> Dict[str, Any] | None:
        if not price_records:
            return None
        target = next((row for row in price_records if row.get("base_date") == snapshot.get("base_date")), price_records[0])
        merged = dict(target)
        merged["market_cap"] = snapshot.get("market_cap")
        merged["outstanding_shares"] = snapshot.get("listed_shares")
        return merged

    @staticmethod
    def _merge_latest_supply_record(supply_records: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> Dict[str, Any] | None:
        if not supply_records:
            return None
        target = next((row for row in supply_records if row.get("base_date") == snapshot.get("base_date")), supply_records[0])
        merged = dict(target)
        merged["foreign_holding_ratio"] = snapshot.get("foreign_holding_ratio")
        return merged

    @staticmethod
    def _parse_number(value) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def _load_master_symbol_map(self) -> Dict[str, Dict[str, Any]]:
        try:
            res = self.loader.client.table("stocks_master").select("symbol, name, market, asset_type").execute()
            symbol_map = {}
            for row in res.data or []:
                symbol = normalize_symbol_value(row.get("symbol"))
                if not symbol:
                    continue
                copied = dict(row)
                copied["symbol"] = symbol
                copied["market"] = _standardize_market(copied.get("market"), copied.get("name"))
                symbol_map[symbol] = copied
            return symbol_map
        except Exception as exc:
            logger.warning(f"Failed to load stocks_master for ranking classification: {exc}")
            return {}

    def _expected_rankings(self) -> list[tuple[str, str, int]]:
        return [
            ("KOSPI", "volume", 30),
            ("KOSPI", "trading_value", 30),
            ("KOSDAQ", "volume", 30),
            ("KOSDAQ", "trading_value", 30),
            ("KOSDAQ", "market_cap", 30),
            ("ETF", "volume", 20),
            ("ETF", "trading_value", 20),
            ("ETN", "volume", 20),
            ("ETN", "trading_value", 20),
        ]

    def _resolve_ranking_market(self, requested_market: str, master_row: Dict[str, Any] | None, name: str | None) -> str | None:
        if requested_market == "KOSPI200":
            return "KOSPI200"
        master_market = _standardize_market((master_row or {}).get("market"), (master_row or {}).get("name") or name)
        if master_market:
            return master_market
        return _standardize_market(requested_market, name)

    def _build_ranking_record(
        self,
        row: Dict[str, Any],
        ranking_market: str,
        rank_type: str,
        available_at: str,
        source: str,
        raw_rank: int | None = None,
        master_row: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
        name = row.get("hts_kor_isnm") or row.get("name") or (master_row or {}).get("name") or symbol
        volume = self._parse_number(row.get("acml_vol") or row.get("volume"))
        trading_value = self._parse_number(row.get("acml_tr_pbmn") or row.get("trading_value"))
        market_cap = self._parse_number(row.get("stck_avls") or row.get("market_cap"))
        change_rate = self._parse_number(row.get("prdy_ctrt") or row.get("change_rate"))
        metric_value = {"volume": volume, "trading_value": trading_value, "market_cap": market_cap}.get(rank_type, volume)
        payload = {
            "symbol": symbol,
            "name": name,
            "market": ranking_market,
            "volume": volume,
            "trading_value": trading_value,
            "market_cap": market_cap,
            "change_rate": change_rate,
            "metric_value": metric_value,
            "available_at": available_at,
            "source": source,
        }
        if raw_rank is not None:
            payload["raw_rank"] = raw_rank
        return payload

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
        raw_records = []
        normalized_records = []
        universe_rows = []
        filtered_records = []

        for idx, row in enumerate(rows or [], 1):
            symbol = normalize_symbol_value(row.get("mksc_shrn_iscd") or row.get("symbol") or row.get("code"))
            if not symbol:
                continue
            master_row = master_symbol_map.get(symbol)
            name = row.get("hts_kor_isnm") or row.get("name") or (master_row or {}).get("name") or symbol
            ranking_market = self._resolve_ranking_market(market, master_row, name)
            if market != "KOSPI200" and ranking_market != market:
                continue
            filtered_records.append((idx, row, symbol, name, master_row, ranking_market))

        for rank, (raw_rank, row, symbol, name, master_row, ranking_market) in enumerate(filtered_records[:limit], 1):
            record = self._build_ranking_record(
                row=row,
                ranking_market=ranking_market,
                rank_type=rank_type,
                available_at=available_at,
                source=source,
                raw_rank=raw_rank,
                master_row=master_row,
            )
            raw_records.append(
                {
                    "source": source,
                    "base_date": base_date,
                    "market": ranking_market,
                    "rank_type": rank_type,
                    "symbol": symbol,
                    "name": name,
                    "raw_rank": raw_rank,
                    "raw_data": json.dumps(
                        {
                            "response_row": row,
                            "requested_market": market,
                            "resolved_market": ranking_market,
                            "master_market": (master_row or {}).get("market"),
                            "asset_type": (master_row or {}).get("asset_type"),
                        },
                        ensure_ascii=False,
                    ),
                    "available_at": available_at,
                }
            )
            normalized_records.append(
                {
                    "base_date": base_date,
                    "market": ranking_market,
                    "rank_type": rank_type,
                    "rank": rank,
                    **record,
                }
            )
            universe_rows.append({"code": symbol, "name": name, "market": ranking_market})

        if raw_records:
            self.loader.upsert_records("raw_market_rankings", raw_records)
        if normalized_records:
            self.loader.upsert_records("normalized_market_rankings_daily", normalized_records)
        return universe_rows

    def _load_latest_valid_price_rows(self) -> tuple[str | None, list[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        try:
            latest_res = (
                self.loader.client.table("normalized_stock_prices_daily")
                .select("base_date")
                .order("base_date", desc=True)
                .limit(1)
                .execute()
            )
            if not latest_res.data:
                return None, [], {}
            latest_date = latest_res.data[0]["base_date"]
            price_res = (
                self.loader.client.table("normalized_stock_prices_daily")
                .select("symbol, volume, trading_value, close_price, market_cap")
                .eq("base_date", latest_date)
                .execute()
            )
            valid_rows = [
                {
                    "symbol": normalize_symbol_value(row.get("symbol")),
                    "volume": row.get("volume"),
                    "trading_value": row.get("trading_value"),
                    "close_price": row.get("close_price"),
                    "market_cap": row.get("market_cap"),
                }
                for row in (price_res.data or [])
                if row.get("close_price") is not None
                and row.get("volume") is not None
                and row.get("trading_value") is not None
            ]
            return latest_date, valid_rows, self._load_master_symbol_map()
        except Exception as exc:
            logger.warning(f"Failed to load latest valid price rows for ranking fallback: {exc}")
        return None, [], {}

    def _build_fallback_rankings_from_valid_prices(
        self,
        latest_date: str,
        valid_rows: List[Dict[str, Any]],
        master_symbol_map: Dict[str, Dict[str, Any]],
        market: str,
        rank_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if rank_type not in {"volume", "trading_value", "market_cap"}:
            return []
        metric_key = rank_type
        filtered = []
        for row in valid_rows:
            symbol = normalize_symbol_value(row.get("symbol"))
            master_row = master_symbol_map.get(symbol)
            if not master_row:
                continue
            resolved_market = self._resolve_ranking_market(market, master_row, master_row.get("name"))
            if resolved_market != market:
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

    def _persist_fallback_ranking_rows(
        self,
        ranking_rows: List[Dict[str, Any]],
        market: str,
        rank_type: str,
        limit: int,
        base_date: str,
    ) -> List[Dict[str, str]]:
        available_at = get_current_kst().isoformat()
        raw_records = []
        normalized_records = []
        universe_rows = []
        for rank, row in enumerate(ranking_rows[:limit], 1):
            raw_records.append(
                {
                    "source": "VALID_PRICE_FALLBACK",
                    "base_date": base_date,
                    "market": market,
                    "rank_type": rank_type,
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "raw_rank": rank,
                    "raw_data": json.dumps(row, ensure_ascii=False),
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
                    "source": "VALID_PRICE_FALLBACK",
                    "available_at": available_at,
                }
            )
            universe_rows.append({"code": row["symbol"], "name": row["name"], "market": market})
        if raw_records:
            self.loader.upsert_records("raw_market_rankings", raw_records)
        if normalized_records:
            self.loader.upsert_records("normalized_market_rankings_daily", normalized_records)
        return universe_rows

    def _ensure_expected_rankings(self) -> None:
        latest_date, valid_rows, master_symbol_map = self._load_latest_valid_price_rows()
        if not latest_date or not valid_rows:
            logger.warning("Skipping fallback ranking generation because no latest valid price rows were found.")
            return

        try:
            existing_res = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("market, rank_type")
                .eq("base_date", latest_date)
                .execute()
            )
            existing = {(row["market"], row["rank_type"]) for row in (existing_res.data or [])}
        except Exception as exc:
            logger.warning(f"Failed to fetch existing ranking combinations: {exc}")
            existing = set()

        for market, rank_type, limit in self._expected_rankings():
            if (market, rank_type) in existing:
                continue
            fallback_rows = self._build_fallback_rankings_from_valid_prices(
                latest_date=latest_date,
                valid_rows=valid_rows,
                master_symbol_map=master_symbol_map,
                market=market,
                rank_type=rank_type,
                limit=limit,
            )
            if fallback_rows:
                self._persist_fallback_ranking_rows(fallback_rows, market, rank_type, limit, latest_date)

    def _fallback_ranking_from_valid_prices(self, market: str, rank_type: str, limit: int) -> List[Dict[str, str]]:
        latest_date, valid_rows, master_symbol_map = self._load_latest_valid_price_rows()
        if not latest_date or not valid_rows:
            return []
        ranking_rows = self._build_fallback_rankings_from_valid_prices(
            latest_date=latest_date,
            valid_rows=valid_rows,
            master_symbol_map=master_symbol_map,
            market=market,
            rank_type=rank_type,
            limit=limit,
        )
        if not ranking_rows:
            return []
        return self._persist_fallback_ranking_rows(ranking_rows, market, rank_type, limit, latest_date)

    async def get_combined_universe(self, auto_backfill: bool = True) -> List[Dict[str, Any]]:
        """
        모든 카테고리의 종목을 수집하여 통합된 리스트를 반환합니다.
        중복된 종목은 하나로 합치고 source_category 필드에 출처를 모두 기록합니다.
        """
        logger.info("스마트 유니버스 동적 업데이트 시작...")
        
        # 6가지 카테고리 비동기 병렬 수집
        tasks = [
            self._load_category_0(), # Custom (Static)
            self._load_category_1(), # KOSPI 거래량 상위 30
            self._load_category_2(), # KOSDAQ 거래량 상위 30
            self._load_category_3(), # KOSDAQ 시가총액 상위 30
            self._load_category_4(), # KOSPI 200 구성 종목 (Top 50)
            self._load_category_5()  # ETF 거래량 상위 20
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._ensure_expected_rankings()
        
        combined_map = {} # code -> {name, market, sources: set}
        
        for i, res in enumerate(results):
            category_id = str(i)
            if isinstance(res, Exception):
                logger.error(f"카테고리 {category_id} 수집 실패: {res}")
                continue
            
            for item in res:
                code = item['code']
                name = item['name']
                if not code: continue
                code = normalize_symbol_value(code)
                
                if code not in combined_map:
                    combined_map[code] = {
                        "code": code,
                        "name": name,
                        "market": _standardize_market(item.get("market"), name),
                        "sources": {category_id},
                    }
                else:
                    combined_map[code]["sources"].add(category_id)
                    combined_map[code]["market"] = self._resolve_market(
                        combined_map[code].get("market"),
                        item.get("market"),
                    )
        
        # 최종 리스트 가공 (Deduplicate & Merge)
        final_universe = []
        for code, data in combined_map.items():
            final_universe.append({
                "symbol": code,
                "name": data["name"],
                "market": data.get("market"),
                "source_category": ",".join(sorted(list(data["sources"])))
            })
            
        logger.info(f"스마트 유니버스 구성 완료: 총 {len(final_universe)} 종목")
        
        # 1. DB stocks_master와 비교하여 신규 편입 종목 식별
        try:
            res = self.loader.client.table("stocks_master").select("symbol").execute()
            existing_symbols = {r["symbol"] for r in res.data} if res.data else set()
        except Exception as e:
            logger.error(f"Failed to fetch stocks_master: {e}")
            existing_symbols = set()
            
        new_symbols = [u["symbol"] for u in final_universe if u["symbol"] not in existing_symbols]
        
        if new_symbols and auto_backfill:
            logger.info(f"새로 식별된 신규 편입 종목: {len(new_symbols)}건. 백필을 시작합니다.")
            await self.trigger_auto_backfill(new_symbols)
        elif new_symbols:
            logger.info(f"Detected {len(new_symbols)} new symbols; auto backfill is disabled for this run.")
        else:
            logger.info("신규 편입 종목이 없습니다. 백필을 생략합니다.")
            
        return final_universe

    def fetch_full_universe(self, target_date=None) -> List[Dict[str, Any]]:
        """Fetch the full KOSPI/KOSDAQ universe used for daily price coverage."""
        min_count = int(self.config.get("pipeline", {}).get("full_universe_min_count", 2000))
        krx_collector = KRXCollector(auth_key=self.config.get("krx", {}).get("auth_key", ""))
        rows = krx_collector.fetch_full_universe(target_date=target_date)
        universe = [
            {
                "symbol": normalize_symbol_value(item["code"]),
                "name": item.get("name") or item["code"],
                "market": _standardize_market(item.get("market"), item.get("name")),
                "source_category": "full_universe",
            }
            for item in rows
            if item.get("market") in {"KOSPI", "KOSDAQ"}
        ]
        if len(universe) < min_count:
            logger.warning(
                f"Full universe count is below guardrail: count={len(universe)}, min_count={min_count}"
            )
        else:
            logger.info(f"Full KOSPI/KOSDAQ universe loaded: {len(universe)} symbols")
        return universe

    async def trigger_auto_backfill(self, symbols: List[str]):
        """Backfill newly discovered symbols with price, supply, and snapshot fields."""
        end_dt = get_current_kst()
        start_dt = end_dt - timedelta(days=90)
        start_date_str = start_dt.strftime("%Y%m%d")
        end_date_str = end_dt.strftime("%Y%m%d")

        for idx, symbol in enumerate(symbols):
            logger.info(f"[{idx+1}/{len(symbols)}] Auto backfill in progress - {symbol}")
            try:
                available_at = get_current_kst().isoformat()
                prices = await self.collector.fetch_ohlcv(
                    symbol,
                    timeframe='D',
                    start_date=start_date_str,
                    end_date=end_date_str,
                    available_at=available_at,
                )
                supply = await self.collector.fetch_investor_trend(symbol, available_at=available_at)
                snapshot = await self.collector.fetch_fundamental_info(
                    symbol,
                    base_date=end_dt.strftime("%Y-%m-%d"),
                    available_at=available_at,
                )

                if snapshot:
                    merged_price = self._merge_latest_price_record(prices, snapshot)
                    if merged_price:
                        self.loader.upsert_records("normalized_stock_prices_daily", [merged_price])

                    merged_supply = self._merge_latest_supply_record(supply, snapshot)
                    if merged_supply:
                        self.loader.upsert_records("normalized_stock_supply_daily", [merged_supply])
            except Exception as e:
                logger.error(f"Auto backfill failed - {symbol}: {e}")

            await asyncio.sleep(0.5)

    async def _load_category_0(self) -> List[Dict[str, str]]:
        combined = {}

        for item in self._load_static_universe():
            combined[item["code"]] = {
                "name": item["name"],
                "market": item.get("market"),
            }

        try:
            res = self.loader.client.table("stocks_master").select("symbol, name, market").eq("is_active", True).execute()
            if res.data:
                for stock in res.data:
                    symbol = normalize_symbol_value(stock["symbol"])
                    if symbol not in combined:
                        combined[symbol] = {
                            "name": stock["name"],
                            "market": _standardize_market(stock.get("market"), stock.get("name")),
                        }
        except Exception as e:
            logger.error(f"Category 0 loading error (stocks_master): {e}")

        return [
            {"code": code, "name": value["name"], "market": value.get("market")}
            for code, value in combined.items()
        ]

    async def _load_category_1(self) -> List[Dict[str, str]]:
        """Category 1 (KOSPI Vol Top 30)"""
        res = await self.collector.fetch_volume_rank(market_code='J')
        return self._persist_rankings(res, "KOSPI", "volume", 30)

    async def _load_category_2(self) -> List[Dict[str, str]]:
        """Category 2 (KOSDAQ Vol Top 30)"""
        res = await self.collector.fetch_volume_rank(market_code='Q')
        return self._persist_rankings(res, "KOSDAQ", "volume", 30)

    async def _load_category_3(self) -> List[Dict[str, str]]:
        """Category 3 (KOSDAQ Cap Top 30)"""
        res = await self.collector.fetch_market_cap_rank(market_code='Q')
        return self._persist_rankings(res, "KOSDAQ", "market_cap", 30)

    async def _load_category_4(self) -> List[Dict[str, str]]:
        """Category 4 (KOSPI 200 Top 50)"""
        # FID_INPUT_ISCD '0001'은 KOSPI 200 인덱스를 의미함
        res = await self.collector.fetch_volume_rank(market_code='J', target_code='0001')
        return self._persist_rankings(res, "KOSPI200", "volume", 50)

    async def _load_category_5(self) -> List[Dict[str, str]]:
        """Category 5 (ETF Leaders): ETF 거래량 상위 20"""
        res = await self.collector.fetch_volume_rank(market_code='T')
        if len(res or []) < 20:
            logger.warning(f"KIS ETF volume rank returned fewer than 20 rows: {len(res or [])}; trying valid-price fallback.")
            fallback = self._fallback_ranking_from_valid_prices("ETF", "volume", 20)
            if fallback:
                return fallback
        return self._persist_rankings(res, "ETF", "volume", 20)
