import json
import os
import asyncio
from typing import List, Dict, Any, Set
from src.collectors.kis.domestic import KISDomesticStockCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

class DynamicUniverseLoader:
    """
    실시간 API 연동형 '스마트 유니버스' 시스템.
    6가지 카테고리(커스텀, 거래량 상위, 시총 상위, 인덱스 구성종목 등)에서 
    종목을 동적으로 수집하고 중복을 제거하여 최적화된 유니버스를 구성합니다.
    """
    def __init__(self, config: Dict[str, Any], collector: KISDomesticStockCollector):
        self.config = config
        self.collector = collector
        self.static_universe_path = "config/stock_universe.json"

    async def get_combined_universe(self) -> List[Dict[str, Any]]:
        """
        모든 카테고리의 종목을 수집하여 통합된 리스트를 반환합니다.
        중복된 종목은 하나로 합치고 source_category 필드에 출처를 모두 기록합니다.
        """
        logger.info("🔄 스마트 유니버스 동적 업데이트 시작...")
        
        # 6가지 카테고리 비동기 병렬 수집
        tasks = [
            self._load_category_0(), # Custom (Static)
            self._load_category_1(), # KOSPI 거래량 상위 30
            self._load_category_2(), # KOSDAQ 거래량 상위 30
            self._load_category_3(), # KOSDAQ 시가총액 상위 30
            self._load_category_4(), # KOSPI 200 구성 종목
            self._load_category_5()  # ETF 거래량 상위 20
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        combined_map = {} # code -> {name, sources: set}
        
        for i, res in enumerate(results):
            category_id = str(i)
            if isinstance(res, Exception):
                logger.error(f"❌ 카테고리 {category_id} 수집 실패: {res}")
                continue
            
            for item in res:
                code = item['code']
                name = item['name']
                if not code: continue
                
                if code not in combined_map:
                    combined_map[code] = {"code": code, "name": name, "sources": {category_id}}
                else:
                    combined_map[code]["sources"].add(category_id)
        
        # 최종 리스트 가공 (Deduplicate & Merge)
        final_universe = []
        for code, data in combined_map.items():
            final_universe.append({
                "symbol": code,
                "name": data["name"],
                "source_category": ",".join(sorted(list(data["sources"])))
            })
            
        logger.info(f"✅ 스마트 유니버스 구성 완료: 총 {len(final_universe)} 종목")
        return final_universe

    async def _load_category_0(self) -> List[Dict[str, str]]:
        """Category 0 (Custom): 기존 static JSON 유니버스"""
        try:
            if not os.path.exists(self.static_universe_path):
                return []
            with open(self.static_universe_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [{"code": s["symbol"], "name": s["name"]} for s in data if s.get("enabled", True)]
        except Exception as e:
            logger.error(f"Category 0 loading error: {e}")
            return []

    async def _load_category_1(self) -> List[Dict[str, str]]:
        """Category 1 (KOSPI Vol Top 30)"""
        res = await self.collector.fetch_volume_rank(market_code='J')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"]} for r in res[:30]]

    async def _load_category_2(self) -> List[Dict[str, str]]:
        """Category 2 (KOSDAQ Vol Top 30)"""
        res = await self.collector.fetch_volume_rank(market_code='Q')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"]} for r in res[:30]]

    async def _load_category_3(self) -> List[Dict[str, str]]:
        """Category 3 (KOSDAQ Cap Top 30)"""
        res = await self.collector.fetch_market_cap_rank(market_code='Q')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"]} for r in res[:30]]

    async def _load_category_4(self) -> List[Dict[str, str]]:
        """Category 4 (KOSPI 200)"""
        # FID_INPUT_ISCD '0001'은 KOSPI 200 인덱스를 의미함
        res = await self.collector.fetch_volume_rank(market_code='J', target_code='0001')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"]} for r in res]

    async def _load_category_5(self) -> List[Dict[str, str]]:
        """Category 5 (ETF Leaders): ETF 거래량 상위 20"""
        res = await self.collector.fetch_volume_rank(market_code='T')
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"]} for r in res[:20]]
