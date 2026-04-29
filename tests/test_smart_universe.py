import asyncio
import json
import os
import sys

sys.path.append(os.getcwd())

from src.collectors.kis import KISAuthManager, KISDomesticStockCollector
from src.collectors.kis.base import KISBaseCollector
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)

async def test_smart_universe():
    logger.info("🚀 스마트 유니버스 수집 테스트 시작...")
    
    with open("config/api_keys.json", "r", encoding="utf-8") as f:
        keys = json.load(f)
    
    config = {
        "kis": keys["kis"],
        "supabase": keys["supabase"]
    }
    
    try:
        auth_mgr = KISAuthManager(config)
        await auth_mgr.initialize()
        
        collector = KISDomesticStockCollector(config, auth_mgr, asyncio.Semaphore(2))
        loader = DynamicUniverseLoader(config, collector)
        
        universe = await loader.get_combined_universe(auto_backfill=False)
        
        if universe:
            logger.info(f"✅ 수집 성공! 총 {len(universe)} 개 종목")
            # 상위 10개 출력하여 소스 카테고리 확인
            for i, stock in enumerate(universe[:10]):
                logger.info(f"[{i+1}] {stock['name']} ({stock['symbol']}) - Sources: {stock['source_category']}")
        else:
            logger.error("❌ 유니버스 수집 결과가 비어있습니다.")
            
    except Exception as e:
        logger.error(f"❌ 오류 발생: {e}", exc_info=True)
    finally:
        await KISBaseCollector.close_session()
        logger.info("🏁 테스트 완료.")

if __name__ == "__main__":
    asyncio.run(test_smart_universe())
