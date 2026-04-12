from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISOverseasStockCollector(KISBaseCollector):
    """해외 주식 시장 데이터 수집기 (미국, 일본 등)"""

    async def fetch_ohlcv(self, symbol: str, exchange: str = 'NASD', timeframe: str = 'D', end_date: str = ""):
        """
        해외 주식 일봉/분봉 수집
        TR_ID: HHDFS76240700 (미국 일봉)
        Exchange Codes: NASD(나스닥), NYSE(뉴욕), AMEX(아멕스), TKSE(도쿄) 등
        """
        # 기본적으로 미국 시장 기준 (HHDFS76240700)
        tr_id = "HHDFS76240700" 
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "GUBN": "0", # 0: 일, 1: 주, 2: 월
            "BYMD": end_date,
            "MODP": "1" # 수정주가
        }
        
        data = await self._request("GET", "/uapi/overseas-stock/v1/quotations/dailyprice", tr_id, params=params)
        if not data or "output2" not in data:
            return []
            
        records = []
        for row in data["output2"]:
            records.append({
                "symbol": symbol,
                "exchange": exchange,
                "base_date": row.get("xymd"),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("clos", 0)),
                "volume": int(float(row.get("tvol", 0))),
                "trading_value": int(float(row.get("tamt", 0)))
            })
            
        await self.upsert_records("stock_prices_daily", records)
        return records
