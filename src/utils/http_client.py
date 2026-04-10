import time
import requests
from typing import Optional, Dict, Any
from .logger import get_logger

logger = get_logger(__name__)

class HttpClient:
    """
    Rate Limit을 준수하고 재시도 로직을 가진 HTTP 클라이언트
    """
    def __init__(self, max_retries: int = 3, backoff_factor: float = 1.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()

    def request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """
        요청을 수행하며 실패 시 백오프 후 재시도합니다.
        """
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, timeout=10, **kwargs)
                response.raise_for_status()  # 4xx, 5xx 에러 발생
                return response
            except requests.exceptions.HTTPError as e:
                # HTTP 에러 (Rate Limit 429 포함)
                if response is not None and response.status_code == 429:
                    sleep_time = self.backoff_factor * (2 ** attempt)
                    logger.warning(f"Rate limited (429). Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"HTTP Error: {e} - {url}")
                    # 운영 가능성을 위해 graceful return (None 처리)
                    return None
            except requests.exceptions.RequestException as e:
                # 연결 등 기타 에러
                logger.error(f"Request Exception: {e} - {url}")
                sleep_time = self.backoff_factor * (2 ** attempt)
                time.sleep(sleep_time)
                
        logger.error(f"Failed to fetch {url} after {self.max_retries} attempts.")
        return None

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        response = self.request("GET", url, params=params, headers=headers)
        if response is not None:
            try:
                return response.json()
            except ValueError:
                # HTML 등 JSON이 아닌 경우엔 조용히 리턴 (세션 확보용 등)
                return None
        return None

    def post(self, url: str, data: Optional[Dict[str, Any]] = None, json: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        response = self.request("POST", url, data=data, json=json, headers=headers)
        if response is not None:
            try:
                return response.json()
            except ValueError:
                logger.error(f"Failed to parse JSON from {url}")
                return None
        return None
