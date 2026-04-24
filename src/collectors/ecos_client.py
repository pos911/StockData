from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst

logger = get_logger(__name__)


@dataclass(frozen=True)
class EcosSeriesRow:
    source: str
    series_id: str
    stat_code: str
    item_code: str
    item_name: Optional[str]
    time: str
    date: str
    value: Optional[float]
    unit: Optional[str]
    cycle: str
    collected_at: str


class EcosClient:
    def __init__(self, api_key: str, base_url: str = "https://ecos.bok.or.kr/api"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

        if not self.api_key or self.api_key.startswith("YOUR_"):
            logger.warning("ECOS API key is missing or placeholder.")

    def build_statistic_url(
        self,
        stat_code: str,
        item_code: str,
        cycle: str,
        start_date: str,
        end_date: str,
        start: int,
        end: int,
    ) -> str:
        return (
            f"{self.base_url}/StatisticSearch/{self.api_key}/json/kr/"
            f"{start}/{end}/{stat_code}/{cycle}/{start_date}/{end_date}/{item_code}/?/?/?"
        )

    def fetch_statistic(
        self,
        stat_code: str,
        item_code: str,
        cycle: str,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
        series_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self.api_key or self.api_key.startswith("YOUR_"):
            raise ValueError("ECOS API key is required.")

        first_payload = self._request(stat_code, item_code, cycle, start_date, end_date, 1, 1)
        search = self._unwrap_search(first_payload, allow_empty=True)
        if search is None:
            return []

        total_count = int(search.get("list_total_count", 0) or 0)
        if total_count == 0:
            return []

        collected_at = get_current_kst().isoformat()
        resolved_series_id = series_id or f"{stat_code}:{item_code}"
        rows: List[Dict[str, Any]] = []

        for start in range(1, total_count + 1, page_size):
            end = min(start + page_size - 1, total_count)
            payload = self._request(stat_code, item_code, cycle, start_date, end_date, start, end)
            search = self._unwrap_search(payload, allow_empty=True)
            if search is None:
                continue
            for row in search.get("row", []) or []:
                normalized = self._normalize_row(
                    row=row,
                    cycle=cycle,
                    series_id=resolved_series_id,
                    stat_code=stat_code,
                    item_code=item_code,
                    collected_at=collected_at,
                )
                if normalized:
                    rows.append(normalized)
        return rows

    def _request(
        self,
        stat_code: str,
        item_code: str,
        cycle: str,
        start_date: str,
        end_date: str,
        start: int,
        end: int,
    ) -> Dict[str, Any]:
        url = self.build_statistic_url(
            stat_code=stat_code,
            item_code=item_code,
            cycle=cycle,
            start_date=start_date,
            end_date=end_date,
            start=start,
            end=end,
        )
        response = self.session.get(url, timeout=20)
        response.raise_for_status()
        return response.json()

    def _unwrap_search(self, payload: Dict[str, Any], allow_empty: bool = False) -> Optional[Dict[str, Any]]:
        result = payload.get("RESULT")
        if result:
            code = result.get("CODE", "")
            message = result.get("MESSAGE", "Unknown ECOS error")
            if code == "INFO-200" and allow_empty:
                logger.warning(f"ECOS returned no data: {message}")
                return None
            raise RuntimeError(f"ECOS API error [{code}]: {message}")

        search = payload.get("StatisticSearch")
        if not search:
            raise RuntimeError("ECOS response is missing StatisticSearch payload.")
        return search

    def _normalize_row(
        self,
        row: Dict[str, Any],
        cycle: str,
        series_id: str,
        stat_code: str,
        item_code: str,
        collected_at: str,
    ) -> Optional[Dict[str, Any]]:
        time_raw = str(row.get("TIME", "")).strip()
        if not time_raw:
            return None

        parsed_date = self.parse_time(time_raw, cycle)
        value = self.parse_value(row.get("DATA_VALUE"))
        item_name = row.get("ITEM_NAME1")
        unit = row.get("UNIT_NAME")

        return EcosSeriesRow(
            source="ECOS",
            series_id=series_id,
            stat_code=stat_code,
            item_code=item_code,
            item_name=item_name,
            time=time_raw,
            date=parsed_date.isoformat(),
            value=value,
            unit=unit,
            cycle=cycle,
            collected_at=collected_at,
        ).__dict__

    @staticmethod
    def parse_value(value: Any) -> Optional[float]:
        if value in (None, "", "."):
            return None
        return float(value)

    @staticmethod
    def parse_time(time_raw: str, cycle: str) -> date:
        cycle = (cycle or "").upper()
        if cycle in {"D", "DD"}:
            return datetime.strptime(time_raw, "%Y%m%d").date()
        if cycle == "M":
            return datetime.strptime(time_raw, "%Y%m").date()
        if cycle == "Q":
            year = int(time_raw[:4])
            quarter = int(time_raw[-1])
            month = {1: 1, 2: 4, 3: 7, 4: 10}[quarter]
            return date(year, month, 1)
        if cycle == "A":
            return date(int(time_raw), 1, 1)
        raise ValueError(f"Unsupported ECOS cycle: {cycle}")
