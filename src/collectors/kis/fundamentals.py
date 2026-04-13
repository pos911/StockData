from datetime import datetime
from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from .mapping import KIS_MAPPING
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISFundamentalsCollector(KISBaseCollector):
    """기업 펀더멘털 및 재무 지표 수집기"""

    async def fetch_financial_statements(self, symbol: str, available_at: Optional[str] = None):
        """
        대차대조표, 손익계산서 주요 항목 수집 (과거 이력 포함)
        TR_ID: FHKST66430300
        """
        m = KIS_MAPPING["financial_ratio"] if "financial_ratio" in KIS_MAPPING else KIS_MAPPING["growth_ratio"]
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_div_cls_code": "0" 
        }
        
        data = await self._request("GET", m["path"], m["tr_id"], params=params)
        if not data or "output" not in data:
            return []
            
        records = []
        for row in data["output"]:
            base_date = row.get("stck_bsop_date")
            if not base_date: continue
            
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}"
            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "revenue": int(row.get("sale_account", 0) or 0),
                "operating_income": int(row.get("op_prfit", 0) or 0),
                "net_income": int(row.get("thst_np", 0) or 0),
                "total_assets": int(row.get("total_assets", 0) or 0),
                "total_liabilities": int(row.get("total_liab", 0) or 0),
                "total_equity": int(row.get("total_equity", 0) or 0),
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        if records:
            await self.upsert_records("normalized_stock_fundamentals", records)
        return records

    async def fetch_valuation_ratios(self, symbol: str, available_at: Optional[str] = None):
        """
        가치평가 및 부채 비율 수집
        TR_ID: FHKST66430600 (안정성), FHKST01010100 (현재가-PER/PBR)
        """
        # 1. PER/PBR 가져오기 (시세 API)
        curr_price_data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", 
                                           params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
        per, pbr = 0.0, 0.0
        if curr_price_data and "output" in curr_price_data:
            per = float(curr_price_data["output"].get("per", 0) or 0)
            pbr = float(curr_price_data["output"].get("pbr", 0) or 0)

        # 2. 안정성지표(부채비율 등) 및 ROE 가져오기
        m = KIS_MAPPING["stability_ratio"]
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_div_cls_code": "0"
        }
        
        data = await self._request("GET", m["path"], m["tr_id"], params=params)
        if not data or "output" not in data:
            return None
            
        output = data["output"]
        row = output[0] if isinstance(output, list) and len(output) > 0 else (output if isinstance(output, dict) else {})
        if not row: return None

        record = {
            "symbol": symbol,
            "base_date": datetime.now().strftime("%Y-%m-%d"),
            "per": per,
            "pbr": pbr,
            "roe": float(row.get("self_cptl_ntin_inrt", 0) or 0), 
            "debt_ratio": float(row.get("lblt_rate", 0) or 0),    
            "source": "KIS",
            "available_at": available_at or datetime.now().isoformat()
        }
            
        await self.upsert_records("normalized_stock_fundamentals_ratios", [record])
        return record

    async def fetch_profitability_ratios(self, symbol: str, available_at: Optional[str] = None):
        """
        수익성 비율 (ROE 등) - 단독 호출용 또는 보완용
        """
        m = KIS_MAPPING["profitability_ratio"]
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_div_cls_code": "0"
        }
        
        data = await self._request("GET", m["path"], m["tr_id"], params=params)
        if not data or "output" not in data:
            return None
            
        output = data["output"]
        row = output[0] if isinstance(output, list) and len(output) > 0 else (output if isinstance(output, dict) else {})
        if not row: return None

        record = {
            "symbol": symbol,
            "base_date": datetime.now().strftime("%Y-%m-%d"),
            "roe": float(row.get("self_cptl_ntin_inrt", 0) or 0),
            "source": "KIS",
            "available_at": available_at or datetime.now().isoformat()
        }
        await self.upsert_records("normalized_stock_fundamentals_ratios", [record])
        return record
