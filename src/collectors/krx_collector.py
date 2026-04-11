from datetime import date, datetime
from typing import Dict, Any, Optional, List
import pandas as pd
import requests
import FinanceDataReader as fdr
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KRXCollector:
    """한국거래소 데이터 수집기 (FinanceDataReader 및 KRX OPEN API 연동)"""
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

    def fetch_market_breadth(self, target_date: date) -> Optional[Dict[str, Any]]:
        """
        KRX OPEN API '전종목 시세' 엔드포인트를 호출하여 Market Breadth 수치를 산출합니다.
        (advances, declines, advancing_volume, declining_volume, unchanged)
        """
        if not self.auth_key:
            logger.error("KRX auth_key is missing. Cannot fetch market breadth.")
            return None

        try:
            # KRX OPEN API '주식 시세 -> 전종목 시세'
            url = "https://openapi.krx.co.kr/svc/apis/sto/stk_bydd_clpr"
            params = {
                "basDd": target_date.strftime("%Y%m%d")
            }
            headers = {"AUTH_KEY": self.auth_key}
            
            logger.info(f"Calling KRX API for market breadth: {params['basDd']}")
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            # KRX OPEN API의 경우 통상 결과 데이터가 'OutBlock_1' 키에 담김
            items = data.get("OutBlock_1", [])
            if not items:
                logger.warning(f"No market breadth data returned from KRX API for {target_date}")
                return None
            
            df = pd.DataFrame(items)
            
            # KOSPI 종목 필터링 (MKT_ID: 'STK'가 유가증권시장)
            if "MKT_ID" in df.columns:
                df = df[df["MKT_ID"] == "STK"]
            
            if df.empty:
                logger.warning("Market breadth data is empty after filtering for KOSPI.")
                return None
            
            # 필요 컬럼 숫자형 변환 (FLUC_RT: 등락률, TDD_VLM: 거래량)
            df["FLUC_RT"] = pd.to_numeric(df["FLUC_RT"], errors='coerce').fillna(0)
            df["TDD_VLM"] = pd.to_numeric(df["TDD_VLM"], errors='coerce').fillna(0)
            
            advances_df = df[df["FLUC_RT"] > 0]
            declines_df = df[df["FLUC_RT"] < 0]
            unchanged_df = df[df["FLUC_RT"] == 0]
            
            result = {
                "advances": int(len(advances_df)),
                "declines": int(len(declines_df)),
                "unchanged": int(len(unchanged_df)),
                "advancing_volume": int(advances_df["TDD_VLM"].sum()),
                "declining_volume": int(declines_df["TDD_VLM"].sum())
            }
            logger.info(f"Market breadth calculated for {target_date}: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to fetch market breadth from KRX OPEN API: {e}")
            return None
