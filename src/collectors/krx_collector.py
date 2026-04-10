from datetime import date
from typing import Dict, Any, Optional
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KRXCollector:
    """한국거래소 정보데이터시스템 수집기 (pykrx 연동)"""
    def __init__(self, auth_key: str = ""):
        self.auth_key = auth_key
        self.client = HttpClient()
        self.base_url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

    def fetch_daily_ohlcv(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """
        일별 OHLCV, 시가총액, 상장주식수, 거래대금 수집 (pykrx 사용)
        """
        try:
            from pykrx import stock
            dt_str = target_date.strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_date(dt_str, dt_str, symbol)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            # 시가총액/상장주식수 추가 조회
            cap_df = stock.get_market_cap_by_date(dt_str, dt_str, symbol)
            
            res = {
                "symbol": symbol,
                "base_date": dt_str,
                "open": int(row["시가"]),
                "high": int(row["고가"]),
                "low": int(row["저가"]),
                "close": int(row["종가"]),
                "volume": int(row["거래량"]),
                "trading_value": int(row["거래대금"]),
            }
            
            if not cap_df.empty:
                cap_row = cap_df.iloc[0]
                res["market_cap"] = int(cap_row["시가총액"])
                res["outstanding_shares"] = int(cap_row["상장주식수"])
            
            return res
        except Exception as e:
            logger.error(f"Error fetching KRX OHLCV for {symbol}: {e}")
            return None

    def fetch_daily_investor_supply(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """
        투자자별 수급 동향 및 공매도 데이터 수집 (pykrx 사용)
        """
        try:
            from pykrx import stock
            dt_str = target_date.strftime("%Y%m%d")
            
            # 투자자별 순매수 (거래소 전체가 아닌 개별 종목 기준)
            df = stock.get_market_net_purchases_of_equities_by_ticker(dt_str, dt_str, "KOSPI") # 일단 코스피 기준 조회가 필요할수도?
            # pykrx의 get_market_net_purchases_of_equities_by_ticker는 해당 날짜의 전종목 순매수를 가져옴.
            # 종목별로 필터링해야함.
            
            # 더 효율적인 방법: get_market_net_purchases_of_equities_by_ticker(from, to, symbol) 사용
            df_inv = stock.get_market_net_purchases_of_equities_by_ticker(dt_str, dt_str, symbol)
            
            if df_inv.empty:
                return None
                
            inv_row = df_inv.iloc[0]
            
            # 외국인 보유 비중
            df_for = stock.get_exhaustion_rates_of_foreign_investment_by_ticker(dt_str, dt_str, symbol)
            for_ratio = 0.0
            if not df_for.empty:
                for_ratio = float(df_for.iloc[0]["지분율"])
                
            # 공매도 현황
            df_short = stock.get_shorting_status_by_date(dt_str, dt_str, symbol)
            short_vol = 0
            short_bal = 0
            if not df_short.empty:
                short_vol = int(df_short.iloc[0]["거래량"])
                short_bal = int(df_short.iloc[0]["잔고수량"])

            return {
                "symbol": symbol,
                "base_date": dt_str,
                "foreign_net_buy": int(inv_row["외국인합계"]),
                "institutional_net_buy": int(inv_row["기관합계"]),
                "individual_net_buy": int(inv_row["개인"]),
                "foreign_holding_ratio": for_ratio,
                "short_volume": short_vol,
                "short_balance": short_bal,
                "lending_balance": 0 # pykrx에서 직접 제공하지 않을 수 있음
            }
        except Exception as e:
            logger.error(f"Error fetching KRX Supply for {symbol}: {e}")
            return None
