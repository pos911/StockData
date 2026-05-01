from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.collectors.ecos_client import EcosClient
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst

logger = get_logger(__name__)


class BaseEcosSeriesCollector:
    def __init__(
        self,
        client: EcosClient,
        loader: SupabaseLoader,
        config_path: str = "config/ecos_series.json",
    ):
        self.client = client
        self.loader = loader
        self.config_path = Path(config_path)

    def load_series_definitions(
        self,
        categories: Sequence[str],
        series_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        with self.config_path.open("r", encoding="utf-8") as handle:
            definitions = json.load(handle)

        category_set = set(categories)
        wanted = set(series_ids or [])
        filtered = []
        for definition in definitions:
            if definition.get("category") not in category_set:
                continue
            if wanted and definition.get("series_id") not in wanted:
                continue
            filtered.append(definition)
        return filtered

    def resolve_start_date(self, series_id: str, explicit_start: Optional[str] = None) -> str:
        if explicit_start:
            return explicit_start

        try:
            result = (
                self.loader.client.table("raw_ecos_macro_daily")
                .select("date")
                .eq("series_id", series_id)
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                latest = date.fromisoformat(result.data[0]["date"])
                return (latest + timedelta(days=1)).strftime("%Y%m%d")
        except Exception as exc:
            logger.warning(f"Failed to resolve incremental start for {series_id}: {exc}")
        return "20100101"

    def collect_definitions(
        self,
        definitions: Sequence[Dict[str, Any]],
        explicit_start: Optional[str] = None,
        explicit_end: Optional[str] = None,
        lookback_days: int = 14,
    ) -> Dict[str, Any]:
        end_date = explicit_end or get_current_kst().date().strftime("%Y%m%d")
        raw_records: List[Dict[str, Any]] = []
        normalized_records: List[Dict[str, Any]] = []
        master_records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        failures: List[str] = []

        for definition in definitions:
            series_id = definition["series_id"]
            start_date = self.resolve_start_date(series_id, explicit_start=explicit_start)
            if start_date > end_date:
                logger.info(f"Skipping ECOS series {series_id}: start_date {start_date} is after {end_date}.")
                continue

            try:
                rows = self.client.fetch_statistic(
                    stat_code=definition["stat_code"],
                    item_code=definition["item_code"],
                    cycle=definition["cycle"],
                    start_date=start_date,
                    end_date=end_date,
                    series_id=series_id,
                )
                if not rows and explicit_start is None:
                    fallback_start = (get_current_kst().date() - timedelta(days=lookback_days)).strftime("%Y%m%d")
                    logger.warning(
                        f"{series_id}: no incremental ECOS rows for {start_date}..{end_date}; "
                        f"retrying lookback fallback {fallback_start}..{end_date}"
                    )
                    rows = self.client.fetch_statistic(
                        stat_code=definition["stat_code"],
                        item_code=definition["item_code"],
                        cycle=definition["cycle"],
                        start_date=fallback_start,
                        end_date=end_date,
                        series_id=series_id,
                    )
                rows = self._deduplicate_by_date(rows)
                if not rows:
                    latest_existing = self._latest_existing_date(series_id)
                    message = (
                        f"{series_id}: no ECOS rows returned for {start_date}..{end_date}; "
                        f"latest_existing={latest_existing or 'none'}"
                    )
                    logger.warning(message)
                    warnings.append(message)
                else:
                    for row in rows:
                        raw_records.append(
                            {
                                "source": "ECOS",
                                "series_id": series_id,
                                "stat_code": definition["stat_code"],
                                "item_code": definition["item_code"],
                                "item_name": row.get("item_name") or definition.get("name_ko"),
                                "date": row["date"],
                                "time_raw": row["time"],
                                "value": row["value"],
                                "unit": row.get("unit") or definition.get("unit"),
                                "cycle": definition["cycle"],
                                "collected_at": row["collected_at"],
                                "raw": row,
                            }
                        )
                        if row["value"] is not None:
                            normalized_records.append(
                                {
                                    "series_id": series_id,
                                    "base_date": row["date"],
                                    "value": row["value"],
                                    "available_at": self._available_at(row["date"]),
                                }
                            )
                master_records.append(self._build_master_record(definition))
            except Exception as exc:
                message = f"{series_id}: {exc}"
                logger.error(f"ECOS collection failed for {message}")
                failures.append(message)

        return {
            "raw_records": raw_records,
            "normalized_records": normalized_records,
            "master_records": self._deduplicate_master(master_records),
            "warnings": warnings,
            "failures": failures,
        }

    def _latest_existing_date(self, series_id: str) -> Optional[str]:
        try:
            result = (
                self.loader.client.table("normalized_macro_series")
                .select("base_date")
                .eq("series_id", series_id)
                .order("base_date", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["base_date"]
        except Exception as exc:
            logger.warning(f"Failed to fetch latest normalized ECOS date for {series_id}: {exc}")
        return None

    @staticmethod
    def _available_at(base_date: str) -> str:
        return datetime.fromisoformat(f"{base_date}T18:30:00+09:00").isoformat()

    @staticmethod
    def _deduplicate_by_date(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            key = (row.get("series_id", ""), row.get("date", ""))
            deduped[key] = row
        return list(deduped.values())

    @staticmethod
    def _deduplicate_master(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}
        for record in records:
            deduped[record["series_id"]] = record
        return list(deduped.values())

    @staticmethod
    def _build_master_record(definition: Dict[str, Any]) -> Dict[str, Any]:
        now = get_current_kst().isoformat()
        return {
            "series_id": definition["series_id"],
            "source": definition["source"],
            "name": definition["name_ko"],
            "name_ko": definition["name_ko"],
            "stat_code": definition["stat_code"],
            "item_code": definition["item_code"],
            "category": definition["category"],
            "unit": definition["unit"],
            "frequency": definition["frequency"],
            "is_active": True,
            "updated_at": now,
        }


class EcosRatesCollector(BaseEcosSeriesCollector):
    def collect(
        self,
        series_ids: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = 14,
    ) -> Dict[str, Any]:
        definitions = self.load_series_definitions(categories=("rates", "credit"), series_ids=series_ids)
        return self.collect_definitions(
            definitions,
            explicit_start=start_date,
            explicit_end=end_date,
            lookback_days=lookback_days,
        )
