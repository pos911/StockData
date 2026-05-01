from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from src.collectors.ecos_rates import BaseEcosSeriesCollector


class EcosFXCollector(BaseEcosSeriesCollector):
    def collect(
        self,
        series_ids: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = 14,
    ) -> Dict[str, Any]:
        definitions = self.load_series_definitions(categories=("fx",), series_ids=series_ids)
        return self.collect_definitions(
            definitions,
            explicit_start=start_date,
            explicit_end=end_date,
            lookback_days=lookback_days,
        )
