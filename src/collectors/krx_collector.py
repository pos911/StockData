from datetime import date, datetime
from typing import Dict, Any, Optional, List
import FinanceDataReader as fdr
import time
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KRXCollector:
    """한국거래소 데이터 수집기 (FinanceDataReader 기반)"""
    def __init__(self, auth_key: str = ""):
        self.auth_key = auth_key
        self.client = HttpClient()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def fetch_daily_ohlcv(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """FinanceDataReader를 이용한 OHLCV 수집"""
        try:
            dt_str = target_date.strftime("%Y-%m-%d")
            df = fdr.DataReader(symbol, dt_str, dt_str)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            return {
                "symbol": symbol,
                "base_date": target_date.strftime("%Y%m%d"),
                "open": int(row.get("Open", 0)),
                "high": int(row.get("High", 0)),
                "low": int(row.get("Low", 0)),
                "close": int(row.get("Close", 0)),
                "volume": int(row.get("Volume", 0)),
                "trading_value": int(row.get("Amount", 0)) if "Amount" in row else 0,
                "market_cap": 0,
                "outstanding_shares": 0
            }
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} via FDR: {e}")
            return None
