from datetime import datetime
from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISBondsCollector(KISBaseCollector):
    """채권 데이터 수집기"""

    async def fetch_bond_price(self, bond_code: str):
        """
        장내/장외 채권 시세 수집
        TR_ID: FHKST01011100
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "B",
            "FID_INPUT_ISCD": bond_code
        }
        
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-bond-price", "FHKST01011100", params=params)
        if not data or "output" not in data:
            return None
            
        row = data["output"]
        record = {
            "symbol": bond_code,
            "base_date": datetime.now().strftime("%Y%m%d"), # 채권 현재가 기준
            "price": float(row.get("stck_prpr", 0)),
            "yield_rate": float(row.get("bond_yield", 0)),
            "volume": int(row.get("acml_vol", 0))
        }
            
        await self.upsert_records("bond_prices", [record])
        return record
