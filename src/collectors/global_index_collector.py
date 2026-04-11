import logging
from datetime import date, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class GlobalIndexCollector:
    """글로벌 인덱스 및 매크로 지표 수집기 (yfinance 연동)"""
    def __init__(self):
        # 매핑 정의 (yfinance 티커)
        self.ticker_map = {
            "usdkrw": "KRW=X",
            "dxy": "DX-Y.NYB",
            "us10y": "^TNX", # 10-Year Treasury Yield
            "nasdaq": "^IXIC",
            "sp500": "^GSPC",
            "sox": "^SOX", # PHLX Semiconductor
            "vix": "^VIX",
            "wti": "CL=F",
            "brent": "BZ=F",
            "gold": "GC=F",
            "copper": "HG=F",
            "bdry": "BDRY"
        }

    def fetch_daily_indices(self, target_date: date) -> Optional[Dict[str, Any]]:
        """
        USDKRW, DXY, US10Y, WTI, Brent, NASDAQ, SP500, SOX, VIX 등 수집
        """
        try:
            import yfinance as yf
            
            # yfinance는 해당 일자의 데이터를 보장하지 않을 수 있으므로 (휴장 등)
            # target_date 기준 3일치 정도를 가져와서 가장 최근 것을 찾음
            start_dt = target_date - timedelta(days=3)
            end_dt = target_date + timedelta(days=1)
            
            res = {"base_date": target_date.strftime("%Y-%m-%d")}
            
            for key, ticker in self.ticker_map.items():
                data = yf.download(ticker, start=start_dt, end=end_dt, progress=False)
                if not data.empty:
                    # target_date와 가장 가까운 (이전 혹은 당일) 데이터 추출
                    # date index가 datetime이므로 target_date.strftime("%Y-%m-%d")와 비교
                    target_str = target_date.strftime("%Y-%m-%d")
                    if target_str in data.index.strftime("%Y-%m-%d"):
                        val = data.loc[data.index.strftime("%Y-%m-%d") == target_str]["Close"].iloc[0]
                    else:
                        val = data["Close"].iloc[-1]
                    
                    # Ensure it's a scalar before conversion
                    if hasattr(val, "iloc"):
                        val = val.iloc[0]
                    res[key] = float(val)
                else:
                    res[key] = None
            
            return res
        except Exception as e:
            logger.error(f"Error fetching Global Indices: {e}")
            return None
