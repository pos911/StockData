from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, Any, Optional

from src.utils.http_client import HttpClient
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst

logger = get_logger(__name__)


class NaverNewsCollector:
    """Naver News search collector."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = HttpClient()
        self.base_url = "https://openapi.naver.com/v1/search/news.json"

        if not self.client_id or self.client_id.startswith("YOUR_"):
            logger.warning("Naver API key is not configured.")

    @staticmethod
    def _parse_pub_date(pub_date: str) -> Optional[datetime]:
        if not pub_date:
            return None
        try:
            parsed = parsedate_to_datetime(pub_date)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=get_current_kst().tzinfo)
            return parsed.astimezone(get_current_kst().tzinfo)
        except (TypeError, ValueError, IndexError):
            return None

    def _filter_recent_items(
        self,
        items: list,
        freshness_hours: int = 12,
        now: Optional[datetime] = None,
    ) -> list:
        now_kst = now or get_current_kst()
        cutoff = now_kst - timedelta(hours=freshness_hours)
        filtered = []

        for item in items or []:
            published_at = self._parse_pub_date(item.get("pubDate"))
            if not published_at:
                continue
            if published_at < cutoff:
                continue

            enriched = dict(item)
            enriched["published_at"] = published_at.isoformat()
            filtered.append(enriched)

        return filtered

    def fetch_news(
        self,
        query: str,
        display: int = 100,
        freshness_hours: int = 12,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch Naver news and keep only items published within the freshness window."""
        if not self.client_id or self.client_id.startswith("YOUR_"):
            return {"items": []}

        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        params = {
            "query": query,
            "display": display,
            "sort": "date",
        }

        logger.info(f"Fetching Naver news for query: {query}")
        response = self.client.get(self.base_url, params=params, headers=headers) or {"items": []}
        items = response.get("items", [])
        filtered_items = self._filter_recent_items(items, freshness_hours=freshness_hours, now=now)
        logger.info(
            f"Filtered Naver news for query '{query}': total={len(items)}, recent={len(filtered_items)}, "
            f"freshness_hours={freshness_hours}"
        )
        response["items"] = filtered_items
        return response
