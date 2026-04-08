from typing import Dict, Any, Optional
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class FREDCollector:
    """FRED 미국 거시 경제 데이터 수집기"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = HttpClient()
        self.base_url = "https://api.stlouisfed.org/fred/series/observations"

        if not self.api_key or self.api_key.startswith("YOUR_"):
            logger.warning("FRED API Key가 설정되지 않았습니다.")

    def fetch_series(self, series_id: str, observation_start: str = None, limit: int = 1000, sort_order: str = "asc") -> Optional[Dict[str, Any]]:
        """
        시계열 데이터 조회 (최신순/과거래순 정렬 및 개수 제한 지원)
        """
        if not self.api_key or self.api_key.startswith("YOUR_"):
            return {"observations": []}
            
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "limit": limit,
            "sort_order": sort_order
        }
        if observation_start:
            params["observation_start"] = observation_start

        logger.info(f"Fetching FRED series: {series_id}")
        return self.client.get(self.base_url, params=params)
