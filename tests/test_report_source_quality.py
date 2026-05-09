from __future__ import annotations

from datetime import date

import pandas as pd

from src.features.generate_features import FeatureGenerator
from src.utils.report_source_quality import analyze_report_views, detect_price_scale_warning


def test_detect_price_scale_warning_flags_out_of_range_price():
    rows = [
        {"base_date": "2026-05-01", "close_price": 210000},
        {"base_date": "2026-05-02", "close_price": 215000},
        {"base_date": "2026-05-03", "close_price": 220000},
        {"base_date": "2026-05-04", "close_price": 225000},
        {"base_date": "2026-05-05", "close_price": 230000},
        {"base_date": "2026-05-08", "close_price": 900000},
    ]
    warnings = detect_price_scale_warning("005930", rows)
    assert "WARN_PRICE_SCALE_ANOMALY" in warnings


def test_detect_price_scale_warning_flags_large_jump():
    rows = [
        {"base_date": "2026-04-01", "close_price": 110000},
        {"base_date": "2026-04-02", "close_price": 111000},
        {"base_date": "2026-04-03", "close_price": 109500},
        {"base_date": "2026-04-04", "close_price": 110500},
        {"base_date": "2026-04-05", "close_price": 112000},
        {"base_date": "2026-05-08", "close_price": 250000},
    ]
    warnings = detect_price_scale_warning("058470", rows)
    assert "WARN_PRICE_JUMP_ANOMALY" in warnings


def test_feature_generator_window_source_status_marks_mixed():
    group = pd.DataFrame(
        [
            {"base_date": "2026-05-01", "source": "KIS"},
            {"base_date": "2026-05-02", "source": "KIS"},
            {"base_date": "2026-05-03", "source": "KIS"},
            {"base_date": "2026-05-04", "source": "KIS"},
            {"base_date": "2026-05-05", "source": "KIS_DETAIL"},
            {"base_date": "2026-05-08", "source": "KIS_DETAIL"},
        ]
    )
    result = FeatureGenerator._window_source_status(group, "2026-05-08", 5)
    assert result["ready"] is True
    assert result["source_mixed"] is True


def test_feature_generator_quality_columns_optional():
    class _Query:
        def select(self, *_args, **_kwargs):
            raise RuntimeError("column missing")

    class _Client:
        def table(self, _name):
            return _Query()

    class _Loader:
        client = _Client()

    generator = FeatureGenerator(_Loader())
    assert generator._feature_quality_columns_available() == (False, False)


def test_analyze_report_views_counts_only_active_watchlist_rows_as_stale():
    class _Execute:
        def __init__(self, data):
            self.data = data

    class _Query:
        def __init__(self, data):
            self._data = data

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            return _Execute(self._data)

    class _Client:
        def table(self, name):
            mapping = {
                "static_stock_universe": [{"symbol": "005930"}, {"symbol": "000660"}],
                "report_watchlist_snapshot_view": [
                    {"symbol": "005930", "base_date": "2026-05-08", "data_status": "FRESH"},
                    {"symbol": "000660", "base_date": "2026-05-07", "data_status": "STALE_BUT_USABLE"},
                    {"symbol": "069960", "base_date": "2026-05-04", "data_status": "STALE"},
                ],
                "report_sector_etf_signal_view": [
                    {"symbol": "396500", "latest_price_date": "2026-05-08", "stale_days": 0, "data_status": "FRESH"},
                    {"symbol": "305720", "latest_price_date": "2026-05-07", "stale_days": 1, "data_status": "STALE_BUT_USABLE"},
                    {"symbol": "091160", "latest_price_date": "2026-05-01", "stale_days": 5, "data_status": "STALE"},
                ],
            }
            return _Query(mapping[name])

    class _Loader:
        client = _Client()

    result = analyze_report_views(_Loader(), date(2026, 5, 8))
    assert result["report_watchlist_snapshot_view_rows"] == 3
    assert result["report_watchlist_active_rows"] == 2
    assert result["stale_watchlist_count"] == 0
    assert result["stale_sector_etf_count"] == 1
