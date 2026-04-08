from typing import Dict, Any, Optional
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class NaverNewsCollector:
    """Naver News 검색 수집기"""
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = HttpClient()
        self.base_url = "https://openapi.naver.com/v1/search/news.json"

        if not self.client_id or self.client_id.startswith("YOUR_"):
            logger.warning("Naver API Key가 설정되지 않았습니다.")

    def fetch_news(self, query: str, display: int = 100) -> Optional[Dict[str, Any]]:
        """
        주식 종목 관련 뉴스 검색
        """
        if not self.client_id or self.client_id.startswith("YOUR_"):
            return {"items": []}
            
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        params = {
            "query": query,
            "display": display,
            "sort": "date"
        }
        
        logger.info(f"Fetching Naver news for query: {query}")
        return self.client.get(self.base_url, params=params, headers=headers)
