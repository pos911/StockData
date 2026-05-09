from __future__ import annotations

from datetime import date

import pandas as pd

from src.features.generate_features import FeatureGenerator
from src.utils import market_data_quality as mdq
from src.utils import report_source_quality as rsq
from src.utils.market_data_quality import is_valid_price_row


class _StubLoader:
    client = object()


def test_is_valid_price_row_rejects_zero_close():
    assert is_valid_price_row({"close_price": 0, "volume": 10, "trading_value": 1000}) is False


def test_is_valid_price_row_accepts_positive_values():
    assert is_valid_price_row({"close_price": 100, "volume": 10, "trading_value": 1000}) is True


def test_is_valid_price_row_require_source_blocks_null_source():
    assert is_valid_price_row(
        {"close_price": 100, "volume": 10, "trading_value": 1000, "source": None},
        require_source=True,
    ) is False


def test_feature_price_filter_excludes_non_trading_and_null_source(monkeypatch):
    generator = FeatureGenerator(_StubLoader())
    monkeypatch.setattr(
        "src.features.generate_features.load_trading_day_strings",
        lambda _loader, _start, _end, exchange_code="XKRX": {"2026-04-10"},
    )
    df = pd.DataFrame(
        [
            {
                "symbol": "000660",
                "base_date": "2026-04-10",
                "source": "KIS_DETAIL",
                "close_price": 1027000,
                "volume": 3134921,
                "trading_value": 3232564390500,
            },
            {
                "symbol": "000660",
                "base_date": "2026-04-12",
                "source": None,
                "close_price": 1027000,
                "volume": 3134921,
                "trading_value": 3232564390500,
            },
        ]
    )
    filtered = generator._filter_feature_price_rows(df, date(2026, 4, 1), date(2026, 5, 8))
    assert filtered["base_date"].tolist() == ["2026-04-10"]
    assert filtered.iloc[0]["source"] == "KIS_DETAIL"


def test_analyze_watchlist_symbol_excludes_invalid_unknown_rows(monkeypatch):
    monkeypatch.setattr(
        rsq,
        "_fetch_price_rows",
        lambda _loader, _symbol, _target, lookback_days=90: [
            {
                "symbol": "000660",
                "base_date": "2026-04-10",
                "source": "KIS_DETAIL",
                "close_price": 1027000,
                "volume": 3134921,
                "trading_value": 3232564390500,
            },
            {
                "symbol": "000660",
                "base_date": "2026-04-12",
                "source": None,
                "close_price": 1027000,
                "volume": 3134921,
                "trading_value": 3232564390500,
            },
            {
                "symbol": "000660",
                "base_date": "2026-05-08",
                "source": "KIS_DETAIL",
                "close_price": 1686000,
                "volume": 100,
                "trading_value": 200,
            },
        ],
    )
    monkeypatch.setattr(rsq, "_fetch_feature_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        rsq,
        "load_trading_day_strings",
        lambda _loader, _start, _end, exchange_code="XKRX": {"2026-04-10", "2026-05-08"},
    )
    monkeypatch.setattr(rsq, "trading_stale_days", lambda *_args, **_kwargs: 0)
    result = rsq.analyze_watchlist_symbol(_StubLoader(), "000660", date(2026, 5, 8))
    assert result["null_source_rows_count"] == 1
    assert result["invalid_non_trading_rows_count"] == 1
    assert result["source_mixed"] is False


def test_scan_invalid_price_rows_detects_invalid_rows(monkeypatch):
    rows = [
        {
            "symbol": "000660",
            "base_date": "2026-04-10",
            "source": "KIS_DETAIL",
            "close_price": 1027000,
            "volume": 3134921,
            "trading_value": 3232564390500,
        },
        {
            "symbol": "000660",
            "base_date": "2026-04-12",
            "source": None,
            "close_price": 1027000,
            "volume": 3134921,
            "trading_value": 3232564390500,
        },
        {
            "symbol": "005930",
            "base_date": "2026-02-05",
            "source": None,
            "close_price": 0,
            "volume": 100,
            "trading_value": 1000,
        },
    ]
    monkeypatch.setattr(mdq, "_fetch_rows", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(
        mdq,
        "_load_master_market_map",
        lambda _loader: {"000660": ("KOSPI", "STOCK"), "005930": ("KOSPI", "STOCK")},
    )
    monkeypatch.setattr(
        mdq,
        "load_trading_day_strings",
        lambda _loader, _start, _end, exchange_code="XKRX": {"2026-04-10"},
    )
    monkeypatch.setattr(
        mdq,
        "get_latest_trading_day_on_or_before",
        lambda _loader, _target_date, exchange_code="XKRX": date(2026, 4, 10),
    )
    result = mdq.scan_invalid_price_rows(_StubLoader(), date(2026, 5, 8), lookback_days=180)
    assert result["non_trading_day_price_rows"] >= 2
    assert result["zero_close_rows"] >= 1
    assert result["null_source_rows"] >= 2
