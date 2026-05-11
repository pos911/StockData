import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

import requests

from src.collectors.kis.auth import KISAuthManager

logger = logging.getLogger(__name__)


class GlobalIndexCollector:
    """Collect global and Korean market index data via yfinance and KIS."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.ticker_map = {
            "usdkrw": "KRW=X",
            "dxy": "DX-Y.NYB",
            "us10y": "^TNX",
            # us3y is sourced from FRED DGS3 in run_daily_macro_pipeline.py.
            "kospi": "^KS11",
            "kosdaq": "^KQ11",
            "nasdaq": "^IXIC",
            "sp500": "^GSPC",
            "sox": "^SOX",
            "vix": "^VIX",
            "wti": "CL=F",
            "brent": "BZ=F",
            "gold": "GC=F",
            "copper": "HG=F",
            "bdry": "BDRY",
        }

    def fetch_daily_indices(self, target_date: date, skip_fields: set[str] | None = None) -> Optional[Dict[str, Any]]:
        try:
            import yfinance as yf

            start_dt = target_date - timedelta(days=7)
            end_dt = target_date + timedelta(days=1)
            result = {"base_date": target_date.strftime("%Y-%m-%d")}
            skipped = skip_fields or set()

            for key, ticker in self.ticker_map.items():
                if key in skipped:
                    result[key] = None
                    result[f"{key}_change_rate"] = None
                    continue
                data = yf.download(ticker, start=start_dt, end=end_dt, progress=False)
                close = self._close_series(data)
                if close is None or close.empty:
                    result[key] = None
                    result[f"{key}_change_rate"] = None
                    continue

                position = self._target_position(close, target_date)
                current = float(close.iloc[position])
                result[key] = current

                if position > 0:
                    previous = float(close.iloc[position - 1])
                    result[f"{key}_change_rate"] = ((current / previous) - 1) * 100 if previous else None
                else:
                    result[f"{key}_change_rate"] = None

            kis_data = self._fetch_korean_market_snapshot(target_date)
            
            for prefix, ranges in [("kospi", (1000, 6500)), ("kosdaq", (300, 1800))]:
                if prefix in skipped:
                    continue
                
                yf_val = result.get(prefix)
                kis_val = kis_data.get(prefix)
                
                yf_valid = yf_val is not None and ranges[0] <= yf_val <= ranges[1]
                kis_valid = kis_val is not None and ranges[0] <= kis_val <= ranges[1]
                
                final_val = None
                source = None
                quality_flag = "OK"
                
                if yf_valid:
                    final_val = yf_val
                    source = "YAHOO"
                    if kis_valid and abs(yf_val - kis_val) / yf_val > 0.05:
                        quality_flag = "SOURCE_MISMATCH"
                elif kis_valid:
                    final_val = kis_val
                    source = "KIS"
                else:
                    quality_flag = "INVALID" if (yf_val or kis_val) else "MISSING"
                    source = "UNKNOWN"
                    
                result[prefix] = final_val
                result[f"{prefix}_source"] = source
                result[f"{prefix}_quality_flag"] = quality_flag
                result[f"{prefix}_source_date"] = target_date.strftime("%Y-%m-%d")
                
                # Assign net buy fields from KIS
                for suffix in ["individual_net_buy", "foreign_net_buy", "institutional_net_buy"]:
                    result[f"{prefix}_{suffix}"] = kis_data.get(f"{prefix}_{suffix}")
                
                # Override change_rate if KIS was chosen
                if final_val == kis_val and not yf_valid and kis_valid:
                    result[f"{prefix}_change_rate"] = kis_data.get(f"{prefix}_change_rate")

            return result
        except Exception as exc:
            logger.error(f"Error fetching Global Indices: {exc}")
            return None

    @staticmethod
    def _close_series(data):
        if data is None or data.empty or "Close" not in data:
            return None
        close = data["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        return close.dropna()

    @staticmethod
    def _target_position(close, target_date: date) -> int:
        target_str = target_date.strftime("%Y-%m-%d")
        index_dates = list(close.index.strftime("%Y-%m-%d"))
        if target_str in index_dates:
            return index_dates.index(target_str)
        return len(close) - 1

    def _fetch_korean_market_snapshot(self, target_date: date) -> Dict[str, Any]:
        # This will be kept around for backward compatibility internally,
        # but we will fetch index levels and flows separately now.
        empty = {
            "kospi": None,
            "kospi_change_rate": None,
            "kosdaq": None,
            "kosdaq_change_rate": None,
            "kospi_individual_net_buy": None,
            "kospi_foreign_net_buy": None,
            "kospi_institutional_net_buy": None,
            "kosdaq_individual_net_buy": None,
            "kosdaq_foreign_net_buy": None,
            "kosdaq_institutional_net_buy": None,
        }
        kis_config = self.config.get("kis", {})
        if not kis_config.get("app_key") or not kis_config.get("app_secret"):
            return empty
        
        try:
            levels = self.fetch_korean_index_levels_from_kis(target_date)
            flows = self.fetch_korean_market_flows_from_kis(target_date)
            return {**empty, **levels, **flows}
        except Exception as exc:
            logger.warning(f"Failed to fetch Korean market snapshot from KIS: {exc}")
            return empty

    def fetch_kis_index_price(self, index_code: str, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        token = asyncio.run(self._get_kis_access_token())
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.config["kis"]["app_key"],
            "appsecret": self.config["kis"]["app_secret"],
            "tr_id": "FHPUP02100000",
            "custtype": "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
        }
        try:
            response = requests.get(
                "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=headers,
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("rt_cd") != "0":
                logger.warning(f"KIS index price API error: {payload.get('msg1')}")
                return None
            return payload.get("output", {})
        except Exception as exc:
            logger.error(f"Error calling KIS inquire-index-price: {exc}")
            return None

    def fetch_korean_index_levels_from_kis(self, target_date: date) -> Dict[str, Any]:
        result = {}
        # 0001 = KOSPI, 1001 = KOSDAQ
        kospi_out = self.fetch_kis_index_price("0001", target_date)
        if kospi_out:
            result["kospi"] = self._to_float(kospi_out.get("bstp_nmix_prpr"))
            result["kospi_change_rate"] = self._to_float(kospi_out.get("bstp_nmix_prdy_ctrt"))
            
        kosdaq_out = self.fetch_kis_index_price("1001", target_date)
        if kosdaq_out:
            result["kosdaq"] = self._to_float(kosdaq_out.get("bstp_nmix_prpr"))
            result["kosdaq_change_rate"] = self._to_float(kosdaq_out.get("bstp_nmix_prdy_ctrt"))
        return result

    def fetch_korean_market_flows_from_kis(self, target_date: date) -> Dict[str, Any]:
        token = asyncio.run(self._get_kis_access_token())
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.config["kis"]["app_key"],
            "appsecret": self.config["kis"]["app_secret"],
            "tr_id": "FHPTJ04040000",
            "custtype": "P",
        }
        target = target_date.strftime("%Y%m%d")
        result = {}
        for prefix, market_code, market_name in (
            ("kospi", "0001", "KSP"),
            ("kosdaq", "1001", "KSQ"),
        ):
            row = self._fetch_market_daily_row(headers, target, market_code, market_name)
            if not row:
                continue
            result[f"{prefix}_individual_net_buy"] = self._to_float(row.get("prsn_ntby_qty"))
            result[f"{prefix}_foreign_net_buy"] = self._to_float(row.get("frgn_ntby_qty"))
            result[f"{prefix}_institutional_net_buy"] = self._to_float(row.get("orgn_ntby_qty"))
        return result

    def _fetch_market_daily_row(
        self,
        headers: Dict[str, str],
        target: str,
        market_code: str,
        market_name: str,
    ) -> Optional[Dict[str, Any]]:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": market_code,
            "FID_INPUT_DATE_1": target,
            "FID_INPUT_ISCD_1": market_name,
            "FID_INPUT_DATE_2": target,
            "FID_INPUT_ISCD_2": market_code,
        }
        response = requests.get(
            "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") != "0":
            raise ValueError(payload.get("msg1", "Unknown KIS market API error"))

        rows = payload.get("output", [])
        if not rows:
            return None

        for row in rows:
            if row.get("stck_bsop_date") == target:
                return row
        return rows[0]

    async def _get_kis_access_token(self) -> str:
        auth = KISAuthManager(self.config)
        return await auth.get_access_token()

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
