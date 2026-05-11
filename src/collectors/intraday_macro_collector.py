import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf
import pandas as pd

from src.collectors.global_index_collector import GlobalIndexCollector
from src.utils.market_sanity import classify_index_quality, is_hard_invalid_index_value

logger = logging.getLogger(__name__)


class IntradayMacroCollector:
    """Collect intraday snapshot for macro/indices/FX."""

    # hard_min / hard_max are wide safety bounds only - catches zero, negative,
    # or absurd parser errors (e.g. string concat).  Normal market moves
    # (KOSPI 7000, 15000, etc.) are never rejected by these bounds.
    SERIES_MAP = {
        "KOSPI":  {"ticker": "^KS11",    "market": "KR",        "hard_min": 100,  "hard_max": 100000, "kis_code": "0001"},
        "KOSDAQ": {"ticker": "^KQ11",    "market": "KR",        "hard_min": 50,   "hard_max": 50000,  "kis_code": "1001"},
        "USDKRW": {"ticker": "KRW=X",    "market": "FX",        "hard_min": 500,  "hard_max": 5000},
        "DXY":    {"ticker": "DX-Y.NYB", "market": "GLOBAL",    "hard_min": 30,   "hard_max": 300},
        "SP500":  {"ticker": "^GSPC",    "market": "US",        "hard_min": 100,  "hard_max": 100000},
        "NASDAQ": {"ticker": "^IXIC",    "market": "US",        "hard_min": 500,  "hard_max": 200000},
        "SOX":    {"ticker": "^SOX",     "market": "US",        "hard_min": 100,  "hard_max": 100000},
        "VIX":    {"ticker": "^VIX",     "market": "US",        "hard_min": 1,    "hard_max": 500},
        "WTI":    {"ticker": "CL=F",     "market": "COMMODITY", "hard_min": 5,    "hard_max": 1000},
        "BRENT":  {"ticker": "BZ=F",     "market": "COMMODITY", "hard_min": 5,    "hard_max": 1000},
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.global_collector = GlobalIndexCollector(self.config)

    def _hard_invalid(self, value: Optional[float], hard_min: float, hard_max: float) -> bool:
        """Hard-bound check — only catches zero/negative or absurd parser errors."""
        if is_hard_invalid_index_value(value):
            return True
        return not (hard_min <= value <= hard_max)

    def fetch_snapshots(self, target_date: date) -> List[Dict[str, Any]]:
        results = []
        base_date_str = target_date.strftime("%Y-%m-%d")
        collected_at = datetime.now(timezone.utc)

        for series_id, info in self.SERIES_MAP.items():
            ticker = info["ticker"]
            market = info["market"]
            hard_min = info["hard_min"]
            hard_max = info["hard_max"]
            kis_code = info.get("kis_code")

            try:
                # 1. KIS Index API (Korean indices only)
                kis_val = None
                kis_change_rate = None
                kis_raw = None
                kis_success = False

                if kis_code:
                    try:
                        kis_out = self.global_collector.fetch_kis_index_price(kis_code, target_date)
                        if kis_out:
                            kis_val_str = kis_out.get("bstp_nmix_prpr")
                            if kis_val_str is not None:
                                kis_val = float(kis_val_str)
                                ctrt_str = kis_out.get("bstp_nmix_prdy_ctrt")
                                kis_change_rate = float(ctrt_str) if ctrt_str else None
                                kis_raw = kis_out
                                kis_success = True
                    except Exception as e:
                        logger.warning(f"Failed to fetch KIS index {kis_code}: {e}")

                # 2. Yahoo Finance (fallback or non-KIS series)
                df = pd.DataFrame()
                try:
                    df = yf.Ticker(ticker).history(period="1d", interval="1m")
                    if df.empty:
                        df = yf.Ticker(ticker).history(period="1d", interval="5m")
                except Exception as e:
                    logger.warning(f"YFinance error for {ticker}: {e}")

                is_fallback_daily = False
                if df.empty:
                    try:
                        df = yf.Ticker(ticker).history(period="5d", interval="1d")
                        is_fallback_daily = True
                    except Exception:
                        pass

                yf_val = None
                yf_change_rate = None
                yf_timestamp = None
                yf_raw: Dict[str, Any] = {}

                if not df.empty:
                    last_row = df.iloc[-1]
                    yf_val = float(last_row["Close"])
                    if len(df) > 1:
                        prev_close = float(df.iloc[-2]["Close"])
                        if prev_close and prev_close > 0:
                            yf_change_rate = ((yf_val / prev_close) - 1.0) * 100.0
                    yf_timestamp = df.index[-1].to_pydatetime().astimezone(timezone.utc)
                    yf_raw = {
                        "Open":   float(last_row.get("Open", 0)),
                        "High":   float(last_row.get("High", 0)),
                        "Low":    float(last_row.get("Low", 0)),
                        "Close":  float(last_row.get("Close", 0)),
                        "Volume": float(last_row.get("Volume", 0)),
                    }

                # 3. Determine final values
                final_val = None
                final_change_rate = None
                source = "UNKNOWN"
                source_symbol = ticker
                observed_at = collected_at
                quality_flag = "MISSING"
                quality_detail: Dict[str, Any] = {}
                raw_data: Dict[str, Any] = {}

                if kis_success:
                    source = "KIS"
                    source_symbol = kis_code
                    raw_data = kis_raw or {}
                    observed_at = collected_at  # KIS real-time; no per-minute timestamp

                    # Record Yahoo as reference only
                    if yf_val is not None:
                        quality_detail["yahoo_fallback_val"] = yf_val

                    if self._hard_invalid(kis_val, hard_min, hard_max):
                        # Zero / negative / absurd parser error
                        quality_flag = "INVALID"
                        quality_detail["raw_value"] = kis_val
                    else:
                        final_val = kis_val
                        final_change_rate = kis_change_rate
                        # Soft anomaly / mismatch check — no upper bound rejection
                        quality_flag = classify_index_quality(
                            value=kis_val,
                            source=source,
                            source_change_rate=kis_change_rate,
                            secondary_value=yf_val,
                        )

                elif yf_val is not None:
                    source = "YAHOO"
                    source_symbol = ticker
                    raw_data = yf_raw
                    try:
                        observed_at = yf_timestamp or collected_at
                    except Exception:
                        observed_at = collected_at
                        quality_detail["timestamp_missing"] = True

                    if self._hard_invalid(yf_val, hard_min, hard_max):
                        quality_flag = "INVALID"
                        quality_detail["raw_value"] = yf_val
                    else:
                        final_val = yf_val
                        final_change_rate = yf_change_rate
                        if is_fallback_daily:
                            quality_flag = "FALLBACK_DAILY"
                        elif kis_code:
                            # KIS was tried but failed
                            quality_flag = "FALLBACK_YAHOO"
                        else:
                            quality_flag = classify_index_quality(
                                value=yf_val,
                                source=source,
                                source_change_rate=yf_change_rate,
                            )

                if final_val is None and quality_flag == "MISSING":
                    logger.warning(f"No data found for {series_id} ({ticker}/{kis_code})")
                    continue

                record = {
                    "observed_at":   observed_at.isoformat(),
                    "base_date":     base_date_str,
                    "series_id":     series_id,
                    "value":         final_val,
                    "change_rate":   final_change_rate,
                    "source":        source,
                    "source_symbol": source_symbol,
                    "market":        market,
                    "quality_flag":  quality_flag,
                    "quality_detail": quality_detail,
                    "raw_data":      raw_data,
                }
                results.append(record)
            except Exception as e:
                logger.error(f"Error fetching intraday for {series_id}: {e}")

        return results
