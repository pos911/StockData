import json
import os
import asyncio
from typing import List, Dict, Any, Set
from datetime import timedelta

from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.krx_collector import KRXCollector
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst
from src.loaders.supabase_loader import SupabaseLoader

logger = get_logger(__name__)

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
                    {"code": item["symbol"], "name": item["name"], "market": item.get("market")}
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
                results.append({"code": symbol, "name": name, "market": item.get("market")})
        return results

    @staticmethod
    def _resolve_market(existing_market: str | None, new_market: str | None) -> str | None:
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
                
                if code not in combined_map:
                    combined_map[code] = {
                        "code": code,
                        "name": name,
                        "market": item.get("market"),
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
                "symbol": item["code"],
                "name": item.get("name") or item["code"],
                "market": item.get("market"),
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
                    if stock["symbol"] not in combined:
                        combined[stock["symbol"]] = {
                            "name": stock["name"],
                            "market": stock.get("market"),
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
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "market": "KOSPI"} for r in res[:30]]

    async def _load_category_2(self) -> List[Dict[str, str]]:
        """Category 2 (KOSDAQ Vol Top 30)"""
        res = await self.collector.fetch_volume_rank(market_code='Q')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "market": "KOSDAQ"} for r in res[:30]]

    async def _load_category_3(self) -> List[Dict[str, str]]:
        """Category 3 (KOSDAQ Cap Top 30)"""
        res = await self.collector.fetch_market_cap_rank(market_code='Q')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "market": "KOSDAQ"} for r in res[:30]]

    async def _load_category_4(self) -> List[Dict[str, str]]:
        """Category 4 (KOSPI 200 Top 50)"""
        # FID_INPUT_ISCD '0001'은 KOSPI 200 인덱스를 의미함
        res = await self.collector.fetch_volume_rank(market_code='J', target_code='0001')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "market": "KOSPI"} for r in res[:50]]

    async def _load_category_5(self) -> List[Dict[str, str]]:
        """Category 5 (ETF Leaders): ETF 거래량 상위 20"""
        res = await self.collector.fetch_volume_rank(market_code='T')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "market": "ETF"} for r in res[:20]]
