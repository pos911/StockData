from typing import Dict, Any, List, Optional
import json
from datetime import datetime
from .base import KISBaseCollector
from .mapping import KIS_MAPPING
from src.utils.logger import get_logger
from datetime import datetime

logger = get_logger(__name__)


def _parse_int(val) -> int:
    """안전한 정수 파싱 (None, 빈 문자열, 비정규 포맷 모두 0으로 처리)"""
    if val is None or val == "":
        return 0
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


def _parse_float(val) -> float:
    """안전한 부동소수점 파싱 (None, 빈 문자열, 비정규 포맷 모두 0.0으로 처리)"""
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0

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
            close = _parse_int(row.get("stck_clpr", 0))
            # 시가총액 자체 계산 (FHPST01740000 404 우회용 Fallback)
            # output1의 lstn_stcn(상장주식수)을 활용, output2에는 없으므로 hts_avls 또는 0 이월 처리
            listed_shares = _parse_int(row.get("lstn_stcn") or row.get("hts_avls") or 0)
            market_cap = close * listed_shares if listed_shares > 0 else None
            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "open_price": _parse_int(row.get("stck_oprc", 0)),
                "high_price": _parse_int(row.get("stck_hgpr", 0)),
                "low_price": _parse_int(row.get("stck_lwpr", 0)),
                "close_price": close,
                "volume": _parse_int(row.get("acml_vol", 0)),
                "trading_value": _parse_int(row.get("acml_tr_pbmn", 0)),
                "market_cap": market_cap,
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        raw_records = []
        for row in records:
            base_date = row.get("base_date", "")
            if len(base_date) == 8 and "-" not in base_date:
                base_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}"
            raw_records.append({
                "source": "KIS",
                "symbol": symbol,
                "base_date": base_date,
                "raw_data": json.dumps(row),
                "collected_at": datetime.now().isoformat(),
                "available_at": datetime.now().isoformat()
            })

        await self.upsert_records("raw_stock_prices_daily", raw_records)
<<<<<<< ours

        # market_cap Forward-fill: None인 경우 이전 row의 값으로 채우기
        prev_cap = None
        for r in records:
            if r["market_cap"] is None and prev_cap is not None:
                r["market_cap"] = prev_cap
                logger.debug(f"[{symbol}] market_cap forward-filled with {prev_cap}")
            if r["market_cap"] is not None:
                prev_cap = r["market_cap"]

        await self.upsert_records("normalized_stock_prices_daily", records)
=======
>>>>>>> theirs
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

            ind = _parse_int(row.get("prsn_ntby_qty", 0))
            inst = _parse_int(row.get("orgn_ntby_qty", 0))
            forgn = _parse_int(row.get("frgn_ntby_qty", 0))
            pen = _parse_int(row.get("pnsn_ntby_qty", 0))
            corp = _parse_int(row.get("etc_corp_ntby_qty", 0))

            if ind == 0 and inst == 0 and forgn == 0 and pen == 0 and corp == 0:
                logger.warning(f"WARNING: Zero flow detected for {symbol}")

            records.append({
                "symbol": symbol,
                "base_date": formatted_date,
                "individual_net_buy": ind,
                "institutional_net_buy": inst,
                "foreign_net_buy": forgn,
                "pension_net_buy": pen,
                "corporate_net_buy": corp,
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat()
            })
            
        raw_records = []
        for row in records:
            base_date = row.get("base_date", "")
            if len(base_date) == 8 and "-" not in base_date:
                base_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}"
            raw_records.append({
                "source": "KIS",
                "symbol": symbol,
                "base_date": base_date,
                "raw_data": json.dumps(row),
                "collected_at": datetime.now().isoformat(),
                "available_at": datetime.now().isoformat()
            })

        await self.upsert_records("raw_stock_supply_daily", raw_records)
<<<<<<< ours
        await self.upsert_records("normalized_stock_supply_daily", records)
=======
>>>>>>> theirs
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
                "short_volume": _parse_int(row.get("ssts_cntg_qty", 0)),
                "short_value": _parse_int(row.get("ssts_tr_pbmn", 0)),
                "short_ratio": _parse_float(row.get("short_sell_vol_rate", 0)),
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

    async def fetch_fundamental_info(self, symbol: str, base_date: str, available_at: Optional[str] = None) -> dict:
        """
        KIS 주식현재가 기본정보 조회 (TR_ID: FHKST01010100)
        hts_avls(시가총액), per, pbr, w52_hgpr(52주최고), w52_lwpr(52주최저), lstn_stcn(상장주식수) 추출
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", params=params)
        if not data or "output" not in data:
            return {}

        out = data["output"]
        record = {
            "symbol": symbol,
            "base_date": base_date,
            "market_cap": _parse_int(out.get("hts_avls", 0)) * 100_000_000,  # 억원 → 원
            "per": _parse_float(out.get("per", 0)),
            "pbr": _parse_float(out.get("pbr", 0)),
            "w52_high": _parse_int(out.get("w52_hgpr", 0)),
            "w52_low": _parse_int(out.get("w52_lwpr", 0)),
            "listed_shares": _parse_int(out.get("lstn_stcn", 0)),
            "source": "KIS",
            "available_at": available_at or datetime.now().isoformat(),
        }
        logger.debug(f"[fetch_fundamental_info] {symbol}: market_cap={record['market_cap']:,}, per={record['per']}, pbr={record['pbr']}")
        return record
