from typing import Dict, Any, Optional
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class TradingEconomicsCollector:
    """TradingEconomics 매크로 데이터 수집기"""
    def __init__(self, client_key: str):
        self.client_key = client_key
        self.client = HttpClient()
        self.base_url = "https://api.tradingeconomics.com"

        if not self.client_key or self.client_key.startswith("YOUR_"):
            logger.warning("TradingEconomics API Key가 설정되지 않았습니다.")

    def fetch_indicator(self, country: str, indicator: str) -> Optional[Dict[str, Any]]:
        """
        특정 국가의 특정 지표 수집
        """
        if not self.client_key or self.client_key.startswith("YOUR_"):
            return []
            
        url = f"{self.base_url}/historical/country/{country}/indicator/{indicator}"
        params = {
            "c": self.client_key,
            "f": "json"
        }
        
        logger.info(f"Fetching TradingEconomics indicator: {indicator} for {country}")
        return self.client.get(url, params=params)
