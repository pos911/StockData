"""
Tests for macro index quality classification and intraday pipeline.
Validates the new dynamic sanity-check approach (no hardcoded upper bounds).
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from src.collectors.global_index_collector import GlobalIndexCollector
from src.collectors.intraday_macro_collector import IntradayMacroCollector
from src.utils.market_sanity import classify_index_quality, is_hard_invalid_index_value


# ──────────────────────────────────────────────────────────────
# market_sanity unit tests
# ──────────────────────────────────────────────────────────────

class TestIsHardInvalid:
    def test_none_is_invalid(self):
        assert is_hard_invalid_index_value(None)

    def test_zero_is_invalid(self):
        assert is_hard_invalid_index_value(0)

    def test_negative_is_invalid(self):
        assert is_hard_invalid_index_value(-100)

    def test_normal_kospi_7822_is_valid(self):
        assert not is_hard_invalid_index_value(7822.24)

    def test_high_kospi_15000_is_valid(self):
        assert not is_hard_invalid_index_value(15000)

    def test_string_is_invalid(self):
        assert is_hard_invalid_index_value("ABC")


class TestClassifyIndexQuality:
    def test_kospi_7822_change_rate_4_is_ok(self):
        assert classify_index_quality(7822.24, "KIS", source_change_rate=4.32) == "OK"

    def test_kospi_15000_change_rate_1_5_is_ok(self):
        assert classify_index_quality(15000.0, "KIS", source_change_rate=1.5) == "OK"

    def test_zero_is_invalid(self):
        assert classify_index_quality(0, "KIS") == "INVALID"

    def test_negative_is_invalid(self):
        assert classify_index_quality(-100, "KIS") == "INVALID"

    def test_anomaly_on_large_change_rate(self):
        # KOSPI 15000 with previous 7800 => ~92% change => ANOMALY
        assert classify_index_quality(15000.0, "KIS", source_change_rate=92.0) == "ANOMALY"

    def test_source_mismatch_on_10pct_deviation(self):
        # KIS 7822, Yahoo 7000 => (7822/7000 - 1) = 11.7% => SOURCE_MISMATCH
        assert classify_index_quality(7822.0, "KIS", secondary_value=7000.0) == "SOURCE_MISMATCH"

    def test_close_secondary_value_is_ok(self):
        # KIS 7822, Yahoo 7830 => ~0.1% => OK
        assert classify_index_quality(7822.0, "KIS", source_change_rate=4.32, secondary_value=7830.0) == "OK"

    def test_high_level_alone_does_not_cause_invalid(self):
        # KOSPI 20000 with normal change_rate must NOT be INVALID
        result = classify_index_quality(20000.0, "KIS", source_change_rate=2.0)
        assert result != "INVALID"
        assert result == "OK"


# ──────────────────────────────────────────────────────────────
# GlobalIndexCollector tests
# ──────────────────────────────────────────────────────────────

@patch('yfinance.download')
@patch.object(GlobalIndexCollector, 'fetch_korean_index_levels_from_kis')
@patch.object(GlobalIndexCollector, 'fetch_korean_market_flows_from_kis')
def test_global_index_collector_kispi_7822_is_ok(mock_flows, mock_levels, mock_yf_download):
    import pandas as pd
    mock_df = pd.DataFrame({'Close': [7820.0]}, index=[pd.to_datetime('2026-05-11')])
    mock_yf_download.return_value = mock_df

    mock_levels.return_value = {'kospi': 7822.24, 'kospi_change_rate': 4.32, 'kosdaq': 1207.34, 'kosdaq_change_rate': -0.03}
    mock_flows.return_value = {}

    collector = GlobalIndexCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    result = collector.fetch_daily_indices(date(2026, 5, 11))

    assert result is not None
    assert result.get('kospi') == 7822.24
    assert result.get('kospi_source') == 'KIS'
    assert result.get('kospi_quality_flag') in ('OK', 'SOURCE_MISMATCH')  # minor mismatch 7822 vs 7820 is OK (< 10%)

@patch('yfinance.download')
@patch.object(GlobalIndexCollector, 'fetch_korean_index_levels_from_kis')
@patch.object(GlobalIndexCollector, 'fetch_korean_market_flows_from_kis')
def test_global_index_collector_zero_kospi_is_invalid(mock_flows, mock_levels, mock_yf_download):
    import pandas as pd
    mock_df = pd.DataFrame({'Close': [7820.0]}, index=[pd.to_datetime('2026-05-11')])
    mock_yf_download.return_value = mock_df

    mock_levels.return_value = {'kospi': 0, 'kospi_change_rate': 0.0, 'kosdaq': 1207.34}
    mock_flows.return_value = {}

    collector = GlobalIndexCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    result = collector.fetch_daily_indices(date(2026, 5, 11))

    assert result is not None
    # zero => falls back to YAHOO
    assert result.get('kospi_source') == 'YAHOO'


# ──────────────────────────────────────────────────────────────
# IntradayMacroCollector tests
# ──────────────────────────────────────────────────────────────

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
@patch.object(GlobalIndexCollector, 'fetch_kis_index_price')
def test_intraday_kospi_7822_from_kis_is_ok(mock_kis_index, mock_yf_ticker):
    import pandas as pd
    # KIS returns 7822.24 with 4.32% change
    mock_kis_index.return_value = {"bstp_nmix_prpr": "7822.24", "bstp_nmix_prdy_ctrt": "4.32"}

    mock_ticker = MagicMock()
    # Yahoo also returns something close
    df = pd.DataFrame({'Close': [7830.0], 'Open': [7800.0], 'High': [7850.0], 'Low': [7780.0], 'Volume': [1000000]},
                      index=[pd.to_datetime('2026-05-11 14:00', utc=True)])
    mock_ticker.history.return_value = df
    mock_yf_ticker.return_value = mock_ticker

    collector = IntradayMacroCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    results = collector.fetch_snapshots(date(2026, 5, 11))

    kospi = next((r for r in results if r['series_id'] == 'KOSPI'), None)
    assert kospi is not None
    assert kospi['value'] == 7822.24
    assert kospi['source'] == 'KIS'
    assert kospi['source_symbol'] == '0001'
    assert kospi['quality_flag'] == 'OK'

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
@patch.object(GlobalIndexCollector, 'fetch_kis_index_price')
def test_intraday_kospi_15000_with_normal_change_is_ok(mock_kis_index, mock_yf_ticker):
    import pandas as pd
    mock_kis_index.return_value = {"bstp_nmix_prpr": "15000.0", "bstp_nmix_prdy_ctrt": "1.5"}

    mock_ticker = MagicMock()
    df = pd.DataFrame({'Close': [14900.0], 'Open': [14800.0], 'High': [15100.0], 'Low': [14700.0], 'Volume': [500000]},
                      index=[pd.to_datetime('2026-05-11 14:00', utc=True)])
    mock_ticker.history.return_value = df
    mock_yf_ticker.return_value = mock_ticker

    collector = IntradayMacroCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    results = collector.fetch_snapshots(date(2026, 5, 11))

    kospi = next((r for r in results if r['series_id'] == 'KOSPI'), None)
    assert kospi is not None
    assert kospi['value'] == 15000.0
    assert kospi['source'] == 'KIS'
    assert kospi['quality_flag'] == 'OK'

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
@patch.object(GlobalIndexCollector, 'fetch_kis_index_price')
def test_intraday_kospi_zero_is_invalid(mock_kis_index, mock_yf_ticker):
    import pandas as pd
    mock_kis_index.return_value = {"bstp_nmix_prpr": "0", "bstp_nmix_prdy_ctrt": "0"}

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    mock_yf_ticker.return_value = mock_ticker

    collector = IntradayMacroCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    results = collector.fetch_snapshots(date(2026, 5, 11))

    kospi = next((r for r in results if r['series_id'] == 'KOSPI'), None)
    # zero from KIS => INVALID, no Yahoo fallback => value=None
    assert kospi is not None
    assert kospi['value'] is None
    assert kospi['quality_flag'] == 'INVALID'

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
@patch.object(GlobalIndexCollector, 'fetch_kis_index_price')
def test_intraday_kospi_kis_fails_yahoo_fallback(mock_kis_index, mock_yf_ticker):
    import pandas as pd
    mock_kis_index.return_value = None  # KIS failure

    mock_ticker = MagicMock()
    df_empty = pd.DataFrame()
    df_daily = pd.DataFrame(
        {'Close': [7800.0], 'Open': [7750.0], 'High': [7850.0], 'Low': [7720.0], 'Volume': [1000000]},
        index=[pd.to_datetime('2026-05-11', utc=True)]
    )
    mock_ticker.history.side_effect = lambda period, interval: df_empty if interval in ('1m','5m') else df_daily
    mock_yf_ticker.return_value = mock_ticker

    collector = IntradayMacroCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    results = collector.fetch_snapshots(date(2026, 5, 11))

    kospi = next((r for r in results if r['series_id'] == 'KOSPI'), None)
    assert kospi is not None
    assert kospi['source'] == 'YAHOO'
    assert kospi['quality_flag'] == 'FALLBACK_DAILY'


# ──────────────────────────────────────────────────────────────
# Basic integration smoke tests
# ──────────────────────────────────────────────────────────────

def test_verify_data_script_callable():
    from scripts.verify_data import _print_intraday_macro_quality
    assert callable(_print_intraday_macro_quality)

def test_run_intraday_macro_pipeline_dry_run():
    from src.jobs.run_intraday_macro_pipeline import run_pipeline
    try:
        run_pipeline(date(2026, 5, 11), dry_run=True)
    except Exception as e:
        pytest.fail(f"dry-run failed with exception: {e}")
