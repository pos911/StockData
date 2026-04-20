from datetime import date, datetime, timedelta
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
        승인 대기 중(404, 403 등)일 경우 에러 대신 경고 로그만 남기고 None을 리턴합니다.
        """
        fallback = self._fetch_market_breadth_pykrx(target_date)
        if fallback:
            return fallback

        if not self.auth_key:
            logger.warning("KRX auth_key is missing and pykrx fallback failed. Skipping market breadth.")
            return None

        try:
            # KRX OPEN API '주식 시세 -> 전종목 시세'
            # 엔드포인트: https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr
            url = "https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr"
            params = {
                "basDd": target_date.strftime("%Y%m%d")
            }
            headers = {"AUTH_KEY": self.auth_key}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            # 승인 전(404) 혹은 권한 부족(403) 시 중단 방지
            if response.status_code != 200:
                logger.warning(f"KRX API pending approval (Status: {response.status_code}). Skipping.")
                return None
            
            data = response.json()
            items = data.get("OutBlock_1", [])
            if not items:
                return None
            
            df = pd.DataFrame(items)
            
            # KOSPI 종목 필터링
            if "MKT_ID" in df.columns:
                df = df[df["MKT_ID"] == "STK"]
            
            if df.empty:
                return None
            
            # 필요 컬럼 숫자형 변환 (FLUC_RT: 등락률, TDD_VLM: 거래량)
            df["FLUC_RT"] = pd.to_numeric(df["FLUC_RT"], errors='coerce').fillna(0)
            df["TDD_VLM"] = pd.to_numeric(df["TDD_VLM"], errors='coerce').fillna(0)
            
            advances_df = df[df["FLUC_RT"] > 0]
            declines_df = df[df["FLUC_RT"] < 0]
            unchanged_df = df[df["FLUC_RT"] == 0]
            
            return {
                "base_date": target_date.strftime("%Y-%m-%d"),
                "advances": int(len(advances_df)),
                "declines": int(len(declines_df)),
                "unchanged": int(len(unchanged_df)),
                "advancing_volume": int(advances_df["TDD_VLM"].sum()),
                "declining_volume": int(declines_df["TDD_VLM"].sum())
            }
            
        except Exception as e:
            logger.warning(f"KRX API pending approval or error: {e}. Skipping.")
            return None

    def _fetch_market_breadth_pykrx(self, target_date: date) -> Optional[Dict[str, Any]]:
        try:
            from pykrx import stock

            for offset in range(0, 7):
                query_date = target_date - timedelta(days=offset)
                frames = []
                for market in ("KOSPI", "KOSDAQ"):
                    df = stock.get_market_ohlcv_by_ticker(query_date.strftime("%Y%m%d"), market=market)
                    if df is not None and not df.empty:
                        frames.append(df)

                if not frames:
                    continue

                df_all = pd.concat(frames)
                change_col = "등락률" if "등락률" in df_all.columns else None
                volume_col = "거래량" if "거래량" in df_all.columns else None
                if not change_col:
                    logger.warning(f"pykrx market breadth missing change-rate column: {list(df_all.columns)}")
                    return None

                change = pd.to_numeric(df_all[change_col], errors="coerce").fillna(0)
                volume = (
                    pd.to_numeric(df_all[volume_col], errors="coerce").fillna(0)
                    if volume_col
                    else pd.Series(0, index=df_all.index)
                )

                advances = change > 0
                declines = change < 0
                unchanged = change == 0
                return {
                    "base_date": query_date.strftime("%Y-%m-%d"),
                    "advances": int(advances.sum()),
                    "declines": int(declines.sum()),
                    "unchanged": int(unchanged.sum()),
                    "advancing_volume": int(volume[advances].sum()),
                    "declining_volume": int(volume[declines].sum()),
                }
            return None
        except Exception as exc:
            logger.warning(f"pykrx market breadth fallback failed: {exc}")
            return None
