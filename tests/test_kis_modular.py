import asyncio
import json
import os
import sys

# 프로젝트 루트를 경로에 추가
sys.path.append(os.getcwd())

from src.collectors.kis import KISAuthManager, KISDomesticStockCollector
from src.collectors.kis.base import KISBaseCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

async def test_kis_pipeline():
    """
    모듈화된 KIS 고안정성 파이프라인의 정상 동작을 검증하는 스모크 테스트
    """
    logger.info("🚀 KIS High-Stability Pipeline Smoke Test 시작...")
    
    # 1. 설정 로드
    config_path = "config/api_keys.json"
    if not os.path.exists(config_path):
        logger.error(f"설정 파일을 찾을 수 없습니다: {config_path}")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    
    # 컬렉터가 기대하는 구조로 매핑
    config = {
        "kis": config_data["kis"],
        "supabase": config_data["supabase"]
    }
    
    try:
        # 2. AuthManager 초기화 (백그라운드 갱신 포함)
        auth_mgr = KISAuthManager(config)
        await auth_mgr.initialize()
        
        # 3. 도메인 컬렉터 초기화
        # KIS 개인 계정 제한인 2 TPS를 준수하도록 세마포어 설정
        semaphore = asyncio.Semaphore(2)
        domestic_collector = KISDomesticStockCollector(config, auth_mgr, semaphore)
        
        # 4. 삼성전자(005930) OHLCV 데이터 수집 테스트
        logger.info("테스트: 삼성전자(005930) 일봉 데이터 수집 중...")
        ohlcv_records = await domestic_collector.fetch_ohlcv("005930", timeframe='D')
        
        if ohlcv_records:
            logger.info(f"✅ OHLCV 수집 성공: {len(ohlcv_records)} 건")
            logger.info(f"샘플 데이터: {ohlcv_records[0]}")
        else:
            logger.warning("⚠️ OHLCV 데이터를 가져오지 못했습니다.")
            
        # 5. 수급 데이터(투자자 매매 동향) 수집 테스트
        logger.info("테스트: 삼성전자(005930) 투자자별 매매 동향 수집 중...")
        trend_records = await domestic_collector.fetch_investor_trend("005930")
        
        if trend_records:
            logger.info(f"✅ 수급 데이터 수집 성공: {len(trend_records)} 건")
        else:
            logger.warning("⚠️ 수급 데이터를 가져오지 못했습니다.")
            
    except Exception as e:
        logger.error(f"❌ 스모크 테스트 중 오류 발생: {e}", exc_info=True)
    finally:
        # 6. 리소스 정리 (세션 종료)
        await KISBaseCollector.close_session()
        logger.info("🏁 테스트 종료 및 세션 클린업 완료.")

if __name__ == "__main__":
    asyncio.run(test_kis_pipeline())
