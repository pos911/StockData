import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
import json

import yfinance as yf

logger = logging.getLogger(__name__)

class IntradayMacroCollector:
    """Collect intraday snapshot for macro/indices/FX."""

    SERIES_MAP = {
        "KOSPI": {"ticker": "^KS11", "market": "KR", "range": (1000, 6500)},
        "KOSDAQ": {"ticker": "^KQ11", "market": "KR", "range": (300, 1800)},
        "USDKRW": {"ticker": "KRW=X", "market": "FX", "range": (900, 2000)},
        "DXY": {"ticker": "DX-Y.NYB", "market": "GLOBAL", "range": (50, 150)},
        "SP500": {"ticker": "^GSPC", "market": "US", "range": (1000, 10000)},
        "NASDAQ": {"ticker": "^IXIC", "market": "US", "range": (5000, 35000)},
        "SOX": {"ticker": "^SOX", "market": "US", "range": (1000, 20000)},
        "VIX": {"ticker": "^VIX", "market": "US", "range": (5, 100)},
        "WTI": {"ticker": "CL=F", "market": "COMMODITY", "range": (20, 200)},
        "BRENT": {"ticker": "BZ=F", "market": "COMMODITY", "range": (20, 200)},
    }

    def fetch_snapshots(self, target_date: date) -> List[Dict[str, Any]]:
        results = []
        base_date_str = target_date.strftime("%Y-%m-%d")
        collected_at = datetime.now(timezone.utc)

        for series_id, info in self.SERIES_MAP.items():
            ticker = info["ticker"]
            market = info["market"]
            min_val, max_val = info["range"]
            
            try:
                # 1m or 5m
                df = yf.Ticker(ticker).history(period="1d", interval="1m")
                if df.empty:
                    df = yf.Ticker(ticker).history(period="1d", interval="5m")

                is_fallback = False
                if df.empty:
                    # Fallback to daily
                    df = yf.Ticker(ticker).history(period="5d", interval="1d")
                    is_fallback = True
                
                if df.empty:
                    logger.warning(f"No data found for {series_id} ({ticker})")
                    continue

                last_row = df.iloc[-1]
                value = float(last_row["Close"])
                change_rate = None
                
                if len(df) > 1:
                    prev_close = float(df.iloc[-2]["Close"])
                    if prev_close and prev_close > 0:
                        change_rate = ((value / prev_close) - 1.0) * 100.0

                timestamp = df.index[-1]
                observed_at = timestamp.to_pydatetime().astimezone(timezone.utc)
                quality_detail = {}
                
                if pd.isna(observed_at) or observed_at is None:
                    observed_at = collected_at
                    quality_detail["timestamp_missing"] = True

                quality_flag = "OK"
                if is_fallback:
                    quality_flag = "FALLBACK_DAILY"

                if not (min_val <= value <= max_val):
                    quality_flag = "INVALID"
                    quality_detail["raw_value"] = value
                    # We might still return value, but invalid.
                    
                raw_data = {
                    "Open": float(last_row.get("Open", 0)),
                    "High": float(last_row.get("High", 0)),
                    "Low": float(last_row.get("Low", 0)),
                    "Close": float(last_row.get("Close", 0)),
                    "Volume": float(last_row.get("Volume", 0)),
                }

                record = {
                    "observed_at": observed_at.isoformat(),
                    "base_date": base_date_str,
                    "series_id": series_id,
                    "value": value,
                    "change_rate": change_rate,
                    "source": "YAHOO",
                    "source_symbol": ticker,
                    "market": market,
                    "quality_flag": quality_flag,
                    "quality_detail": quality_detail,
                    "raw_data": raw_data
                }
                results.append(record)
            except Exception as e:
                logger.error(f"Error fetching intraday for {series_id}: {e}")

        return results

import pandas as pd
