from typing import Dict, Any, List, Optional
from .base import KISBaseCollector
from .mapping import KIS_MAPPING
from src.utils.logger import get_logger
from datetime import datetime

logger = get_logger(__name__)

class KISDomesticStockCollector(KISBaseCollector):
    """국내 주식 시장 데이터 수집기"""

    async def fetch_ohlcv(self, symbol: str, timeframe: str = 'D', start_date: str = "", end_date: str = "", available_at: Optional[str] = None):
        """
        일봉/분봉 가격 및 거래량 데이터 수집
        TR_ID: FHKST03010100 (일별), FHKST03010200 (분별)
        """
        m = KIS_MAPPING["ohlcv"]
        tr_id = m["tr_id"] if timeframe == 'D' else "FHKST03010200"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": timeframe,
            "FID_ORG_ADJ_PRC": "1" # 수정주가 적용
        }
        
        data = await self._request("GET", m["path"], tr_id, params=params)
        if not data or "output2" not in data:
            return []
            
        records = []
        for row in data["output2"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "open_price": int(row.get("stck_oprc", 0)),
                "high_price": int(row.get("stck_hgpr", 0)),
                "low_price": int(row.get("stck_lwpr", 0)),
                "close_price": int(row.get("stck_clpr", 0)),
                "volume": int(row.get("acml_vol", 0)),
                "trading_value": int(row.get("acml_tr_pbmn", 0)),
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        await self.upsert_records("normalized_stock_prices_daily", records)
        return records

    async def fetch_investor_trend(self, symbol: str, available_at: Optional[str] = None):
        """
        투자자별(외인, 기관, 연기금 등) 순매수 동향
        TR_ID: FHKST01010900
        """
        m = KIS_MAPPING["investor_trend"]
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol
        }
        data = await self._request("GET", m["path"], m["tr_id"], params=params)
        if not data or "output" not in data:
            return []
            
        records = []
        for row in data["output"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            
            def _parse_int(val):
                return int(val) if val else 0
                
            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "individual_net_buy": _parse_int(row.get("prsn_ntby_qty", 0)),
                "institutional_net_buy": _parse_int(row.get("orgn_ntby_qty", 0)),
                "foreign_net_buy": _parse_int(row.get("frgn_ntby_qty", 0)),
                "pension_net_buy": _parse_int(row.get("pnsn_ntby_qty", 0)),
                "corporate_net_buy": _parse_int(row.get("etc_corp_ntby_qty", 0)),
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        await self.upsert_records("normalized_stock_supply_daily", records)
        return records

    async def fetch_short_selling(self, symbol: str, available_at: Optional[str] = None):
        """
        공매도 일별 추이 및 잔고 데이터
        TR_ID: FHKST01010400 (Short Volume/Price)
        """
        m = KIS_MAPPING["short_selling"]
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d")
        }
        data = await self._request("GET", m["path"], m["tr_id"], params=params)
        if not data or "output2" not in data:
            return []
            
        records = []
        for row in data["output2"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "short_volume": int(row.get("ssts_cntg_qty", 0)), # 공식 필드명: ssts_cntg_qty
                "short_value": int(row.get("ssts_tr_pbmn", 0)),   # 공식 필드명: ssts_tr_pbmn
                "short_ratio": float(row.get("short_sell_vol_rate", 0)),
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        await self.upsert_records("normalized_stock_short_selling", records)
        return records

    async def fetch_volume_rank(self, market_code: str = 'J', target_code: str = '0000'):
        """
        [국내주식] 거래량 순위 조회 (TR_ID: FHPST01710000)
        market_code: J(코스피), Q(코스닥), T(ETF)
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": target_code,
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "0",
            "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_cnt": ""
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", params=params)
        return data.get("output", []) if data else []

    async def fetch_market_cap_rank(self, market_code: str = 'J'):
        """
        [국내주식] 시가총액 순위 조회 (TR_ID: FHPST01740000)
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_COND_SCR_DIV_CODE": "20174",
            "FID_DIV_CLS_CODE": "0",
            "FID_INPUT_ISCD": "0000"
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/market-cap", "FHPST01740000", params=params)
        return data.get("output", []) if data else []
