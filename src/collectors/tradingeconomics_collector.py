from typing import Any, List

import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TradingEconomicsCollector:
    def __init__(self, client_key: str = ""):
        self.client_key = (client_key or "").strip()
        self.base_url = "https://api.tradingeconomics.com"

    def fetch_indicator(self, country: str, indicator: str) -> List[Any]:
        if not self.client_key:
            logger.warning("TradingEconomics client key is not configured.")
            return []

        url = f"{self.base_url}/country/{country}/{indicator}"
        params = {"c": self.client_key, "f": "json"}

        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else [data]
        except Exception as exc:
            logger.warning(f"TradingEconomics request failed for {country}/{indicator}: {exc}")
            return []
