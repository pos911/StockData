from datetime import datetime
from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISFundamentalsCollector(KISBaseCollector):
    """기업 펀더멘털 및 재무 지표 수집기"""

    async def fetch_financial_statements(self, symbol: str):
        """
        대차대조표, 손익계산서 주요 항목 수집
        TR_ID: FHKST66430300
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_DIV_CLS_CODE": "0" # 0: 전체, 1: 요약
        }
        
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-financial-statement", "FHKST66430300", params=params)
        if not data or "output" not in data:
            return []
            
        records = []
        for row in data["output"]:
            records.append({
                "symbol": symbol,
                "base_date": row.get("stck_bsop_date"),
                "revenue": int(row.get("sale_account", 0)),
                "operating_income": int(row.get("op_prfit", 0)),
                "net_income": int(row.get("thst_np", 0)),
                "total_assets": int(row.get("total_assets", 0)),
                "total_liabilities": int(row.get("total_liab", 0)),
                "total_equity": int(row.get("total_equity", 0))
            })
            
        await self.upsert_records("stock_fundamentals", records)
        return records

    async def fetch_valuation_ratios(self, symbol: str):
        """
        가치평가 비율 (PER, PBR, PSR 등)
        TR_ID: FHKST01011800
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol
        }
        
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-stability", "FHKST01011800", params=params)
        if not data or "output" not in data:
            return None
            
        row = data["output"]
        record = {
            "symbol": symbol,
            "base_date": datetime.now().strftime("%Y%m%d"),
            "per": float(row.get("per", 0)),
            "pbr": float(row.get("pbr", 0)),
            "roe": float(row.get("roe", 0)),
            "debt_ratio": float(row.get("lblt_rate", 0))
        }
            
        await self.upsert_records("stock_fundamentals_ratios", [record])
        return record
