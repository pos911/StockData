from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISDerivativesCollector(KISBaseCollector):
    """국내 선물/옵션 데이터 수집기"""

    async def fetch_futures_ohlcv(self, symbol: str, timeframe: str = 'D'):
        """
        선물 일봉/분봉 수집
        TR_ID: FHKIF03020100
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "F",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": timeframe,
            "FID_ORG_ADJ_PRC": "0"
        }
        
        data = await self._request("GET", "/uapi/domestic-future/v1/quotations/inquire-daily-chartprice", "FHKIF03020100", params=params)
        if not data or "output2" not in data:
            return []
            
        records = []
        for row in data["output2"]:
            records.append({
                "symbol": symbol,
                "base_date": row.get("stck_bsop_date"),
                "open": float(row.get("futs_oprc", 0)),
                "high": float(row.get("futs_hgpr", 0)),
                "low": float(row.get("futs_lwpr", 0)),
                "close": float(row.get("futs_clpr", 0)),
                "volume": int(row.get("acml_vol", 0)),
                "open_interest": int(row.get("open_intrs", 0))
            })
            
        await self.upsert_records("derivatives_prices", records)
        return records

    async def fetch_options_ohlcv(self, symbol: str, timeframe: str = 'D'):
        """
        옵션 일봉/분봉 수집
        TR_ID: FHKIO03020100
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "O",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": timeframe,
            "FID_ORG_ADJ_PRC": "0"
        }
        
        data = await self._request("GET", "/uapi/domestic-option/v1/quotations/inquire-daily-chartprice", "FHKIO03020100", params=params)
        if not data or "output2" not in data:
            return []
            
        records = []
        for row in data["output2"]:
            records.append({
                "symbol": symbol,
                "base_date": row.get("stck_bsop_date"),
                "open": float(row.get("optn_oprc", 0)),
                "high": float(row.get("optn_hgpr", 0)),
                "low": float(row.get("optn_lwpr", 0)),
                "close": float(row.get("optn_clpr", 0)),
                "volume": int(row.get("acml_vol", 0)),
                "open_interest": int(row.get("open_intrs", 0))
            })
            
        await self.upsert_records("derivatives_prices", records)
        return records
