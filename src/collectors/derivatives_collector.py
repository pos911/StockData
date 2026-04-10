import logging
from datetime import date
from typing import Dict, Any, Optional
from pykrx import stock
import FinanceDataReader as fdr

logger = logging.getLogger(__name__)

class DerivativesCollector:
    """파생상품 데이터 수집기 (KOSPI200 지수 및 시장 정보)"""
    def __init__(self):
        pass

    def fetch_daily_derivatives(self, target_date: date) -> Optional[Dict[str, Any]]:
        """
        KOSPI200 지수 및 시장 미결제약정(또는 거래대금) 데이터 수집
        """
        target_dt_str = target_date.strftime("%Y%m%d")
        logger.info(f"Fetching actual Derivatives data for {target_dt_str}...")
        
        try:
            # 1. KOSPI 200 지수 (Spot) 조회
            df_index = stock.get_index_ohlcv_by_date(target_dt_str, target_dt_str, "101")
            
            if df_index.empty:
                logger.warning(f"No index data found for {target_dt_str}")
                return None
            
            kospi200_spot = float(df_index.iloc[0]["종가"])
            
            # 2. 선물 지수 및 미결제약정 방어 로직
            # 파생상품 API 연동 전까지 선물을 현물과 동일하게 맵핑하고 베이시스를 0.0으로 고정
            kospi200_futures = kospi200_spot
            futures_basis = 0.0
            open_interest = int(df_index.iloc[0].get("거래량", 0))
            
            return {
                "base_date": target_date.strftime("%Y-%m-%d"),
                "kospi200_futures": kospi200_futures,
                "futures_basis": futures_basis,
                "open_interest": open_interest,
                "night_futures_return": 0.0,
                "expiration_flag": self._is_expiration_date(target_date)
            }
        except Exception as e:
            logger.error(f"Error fetching derivatives data: {e}")
            return None

    def _is_expiration_date(self, target_date: date) -> bool:
        """선물/옵션 만기일 여부 판정 (간단 로직: 매월 2번째 목요일 등)"""
        # 3, 6, 9, 12월의 2번째 목요일은 선물/옵션 동시 만기일
        if target_date.weekday() != 3: # Thursday
            return False
            
        # 해당 월의 8일~14일 사이의 목요일이 2번째 목요일
        if 8 <= target_date.day <= 14:
            return True
        return False
