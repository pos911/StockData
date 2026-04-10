from datetime import date, datetime
from typing import Dict, Any, Optional, List
import requests
import json
import time

from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KISCollector:
    """한국투자증권(KIS) Open API 기반 수집기"""
    def __init__(self, config: Dict[str, str]):
        self.app_key = config.get("app_key", "")
        self.app_secret = config.get("app_secret", "")
        self.base_url = "https://openapi.koreainvestment.com:9443"
        self.client = HttpClient()
        self._access_token = None
        self._token_expires_at = 0

    def _get_token(self) -> str:
        """Access Token 발급 및 캐싱 (유효기간 24시간)"""
        now = time.time()
        if self._access_token and self._token_expires_at > now:
            return self._access_token
            
        url = f"{self.base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
            res.raise_for_status()
            token_data = res.json()
            self._access_token = token_data["access_token"]
            # To be safe, set expires in 23 hours
            self._token_expires_at = now + (23 * 3600)
            logger.info("Successfully fetched KIS access token.")
            return self._access_token
        except Exception as e:
            logger.error(f"Failed to fetch KIS token: {e}")
            raise

    def fetch_daily_investor_supply(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """특정 일자의 투자자별 수급 데이터 단건 조회"""
        try:
            token = self._get_token()
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
            
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010900",
                "custtype": "P"
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol
            }
            
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code != 200:
                logger.error(f"KIS API Error {res.status_code}: {res.text}")
                return None
                
            data = res.json()
            target_dt_str = target_date.strftime("%Y%m%d")
            
            if "output" in data:
                for row in data["output"]:
                    if row.get("stck_bsop_date") == target_dt_str:
                        return {
                            "symbol": symbol,
                            "base_date": target_dt_str,
                            "foreign_net_buy": int(row.get("frgn_ntby_qty", 0)),
                            "institutional_net_buy": int(row.get("orgn_ntby_qty", 0)),
                            "individual_net_buy": int(row.get("prsn_ntby_qty", 0)),
                            "foreign_holding_ratio": 0.0, # Not directly provided in this specific API endpoint
                            "short_volume": 0,
                            "short_balance": 0,
                            "lending_balance": 0
                        }
            
            return None
        except Exception as e:
            logger.error(f"Error fetching daily supply for {symbol} via KIS: {e}")
            return None

    def fetch_supply_history(self, symbol: str, pages: int = 40) -> List[Dict[str, Any]]:
        """여러 일자의 투자자별 수급 이력을 대량 조회 (단순 반복 조회 방식)
        Note: 페이지네이션(연속조회) 구현 대신 기본 조회(최근 30일)까지만 처리하도록 단순화 
        또는 API 스펙이 허용하는 선에서 최근 이력 반환
        """
        results = []
        try:
            token = self._get_token()
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
            
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": "FHKST01010900",
                "custtype": "P"
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol
            }
            
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if "output" in data:
                    for row in data["output"]:
                        results.append({
                            "symbol": symbol,
                            "base_date": row.get("stck_bsop_date", ""),
                            "foreign_net_buy": int(row.get("frgn_ntby_qty", 0)),
                            "institutional_net_buy": int(row.get("orgn_ntby_qty", 0)),
                            "individual_net_buy": int(row.get("prsn_ntby_qty", 0)),
                            "foreign_holding_ratio": 0.0,
                            "short_volume": 0,
                            "short_balance": 0,
                            "lending_balance": 0
                        })
            return results
        except Exception as e:
            logger.error(f"Error bulk fetching supply for {symbol} via KIS: {e}")
            return results
