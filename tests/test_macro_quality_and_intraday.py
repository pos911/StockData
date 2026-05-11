import pytest
import sys
from datetime import date
from unittest.mock import patch, MagicMock

from src.collectors.global_index_collector import GlobalIndexCollector
from src.collectors.intraday_macro_collector import IntradayMacroCollector

@patch('yfinance.download')
@patch.object(GlobalIndexCollector, 'fetch_korean_index_levels_from_kis')
@patch.object(GlobalIndexCollector, 'fetch_korean_market_flows_from_kis')
def test_global_index_collector_invalid_kospi(mock_flows, mock_levels, mock_yf_download):
    # Setup mock Yahoo Finance returning None or missing for KOSPI
    import pandas as pd
    mock_df = pd.DataFrame({'Close': [None]}, index=[pd.to_datetime('2026-05-11')])
    mock_yf_download.return_value = mock_df
    
    mock_levels.return_value = {
        'kospi': 7822.24,
        'kosdaq': 1207.34
    }
    mock_flows.return_value = {}
    
    collector = GlobalIndexCollector({"kis": {"app_key": "dummy", "app_secret": "dummy"}})
    result = collector.fetch_daily_indices(date(2026, 5, 11))
    
    assert result is not None
    # 7822.24 is out of range (1000~6500), should be INVALID
    assert result.get('kospi_quality_flag') == 'INVALID'
    assert result.get('kosdaq_quality_flag') == 'KIS' or result.get('kosdaq_quality_flag') == 'OK'
    assert result.get('kosdaq_source') == 'KIS'

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
@patch.object(GlobalIndexCollector, 'fetch_kis_index_price')
def test_intraday_macro_collector(mock_kis_index, mock_yf_ticker):
    # Test 1: KIS fails, Yahoo fallback
    import pandas as pd
    mock_kis_index.return_value = None
    
    mock_ticker_instance = MagicMock()
    def side_effect(period, interval):
        if interval in ['1m', '5m']:
            return pd.DataFrame()
        else:
            return pd.DataFrame({'Close': [2700.0], 'Open': [2600.0]}, index=[pd.to_datetime('2026-05-11')])
            
    mock_ticker_instance.history.side_effect = side_effect
    mock_yf_ticker.return_value = mock_ticker_instance
    
    collector = IntradayMacroCollector({})
    results = collector.fetch_snapshots(date(2026, 5, 11))
    
    # Check KOSPI is FALLBACK_YAHOO
    kospi_res = [r for r in results if r['series_id'] == 'KOSPI'][0]
    assert kospi_res['source'] == 'YAHOO'
    assert kospi_res['quality_flag'] == 'FALLBACK_DAILY' # Since we mocked fallback to 5d 1d interval
    
    # Now test KIS succeeds with out of bounds value
    mock_kis_index.return_value = {"bstp_nmix_prpr": "7822.24", "bstp_nmix_prdy_ctrt": "4.0"}
    results2 = collector.fetch_snapshots(date(2026, 5, 11))
    kospi_res2 = [r for r in results2 if r['series_id'] == 'KOSPI'][0]
    assert kospi_res2['source'] == 'KIS'
    assert kospi_res2['quality_flag'] == 'INVALID'

def test_verify_data_script_execution():
    from scripts.verify_data import _print_intraday_macro_quality
    assert callable(_print_intraday_macro_quality)

def test_run_intraday_macro_pipeline_dry_run():
    from src.jobs.run_intraday_macro_pipeline import run_pipeline
    try:
        run_pipeline(date(2026, 5, 11), dry_run=True)
    except Exception as e:
        pytest.fail(f"dry-run failed with exception: {e}")
