import pytest
import sys
from datetime import date
from unittest.mock import patch, MagicMock

from src.collectors.global_index_collector import GlobalIndexCollector
from src.collectors.intraday_macro_collector import IntradayMacroCollector

@patch('yfinance.download')
@patch.object(GlobalIndexCollector, '_fetch_korean_market_snapshot')
def test_global_index_collector_invalid_kospi(mock_fetch_korean, mock_yf_download):
    # Setup mock Yahoo Finance returning None or missing for KOSPI
    import pandas as pd
    mock_df = pd.DataFrame({'Close': [None]}, index=[pd.to_datetime('2026-05-11')])
    mock_yf_download.return_value = mock_df
    
    # Setup KIS snapshot returning invalid KOSPI
    mock_fetch_korean.return_value = {
        'kospi': 7822.24,
        'kosdaq': 1207.34
    }
    
    collector = GlobalIndexCollector({})
    result = collector.fetch_daily_indices(date(2026, 5, 11))
    
    assert result is not None
    # 7822.24 is out of range (1000~6500), should be INVALID
    assert result.get('kospi_quality_flag') == 'INVALID'
    assert result.get('kosdaq_quality_flag') == 'KIS' or result.get('kosdaq_quality_flag') == 'OK' # Since 1207 is valid
    assert result.get('kosdaq_source') == 'KIS'

@patch('src.collectors.intraday_macro_collector.yf.Ticker')
def test_intraday_macro_collector(mock_yf_ticker):
    # Mock returning an empty intraday but a valid daily (Fallback)
    import pandas as pd
    
    mock_ticker_instance = MagicMock()
    def side_effect(period, interval):
        if interval in ['1m', '5m']:
            return pd.DataFrame()
        else:
            return pd.DataFrame({'Close': [1466.0], 'Open': [1460.0]}, index=[pd.to_datetime('2026-05-11')])
            
    mock_ticker_instance.history.side_effect = side_effect
    mock_yf_ticker.return_value = mock_ticker_instance
    
    collector = IntradayMacroCollector()
    results = collector.fetch_snapshots(date(2026, 5, 11))
    
    assert len(results) > 0
    # Should flag USDKRW as FALLBACK_DAILY
    # But wait, it affects all series because the mock is identical for all
    usdkrw_res = [r for r in results if r['series_id'] == 'USDKRW'][0]
    
    assert usdkrw_res['value'] == 1466.0
    assert usdkrw_res['quality_flag'] == 'FALLBACK_DAILY'
    
def test_verify_data_script_execution():
    from scripts.verify_data import _print_intraday_macro_quality
    assert callable(_print_intraday_macro_quality)

def test_run_intraday_macro_pipeline_dry_run():
    from src.jobs.run_intraday_macro_pipeline import run_pipeline
    try:
        run_pipeline(date(2026, 5, 11), dry_run=True)
    except Exception as e:
        pytest.fail(f"dry-run failed with exception: {e}")
