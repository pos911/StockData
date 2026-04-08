import json
from datetime import datetime
from typing import Dict, Any, Optional
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KISCollector:
    """한국투자증권 OpenAPI 수집기"""
    def __init__(self, api_key: str, api_secret: str, account_no: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account_no = account_no
        self.client = HttpClient()
        self.base_url = "https://openapi.koreainvestment.com:9443"
        self.token_file = "config/kis_token.json"
        self.access_token = self._load_token()

    def _load_token(self) -> Optional[str]:
        """파일에서 기존 토큰 로드"""
        try:
            import os
            if os.path.exists(self.token_file):
                with open(self.token_file, "r") as f:
                    data = json.load(f)
                    # 유효기간 검증 로직은 추후 추가 (현재는 단순히 로드)
                    return data.get("access_token")
        except Exception:
            pass
        return None

    def _save_token(self, token: str):
        """환경설정 파일에 토큰 저장"""
        try:
            with open(self.token_file, "w") as f:
                json.dump({"access_token": token, "updated_at": datetime.now().isoformat()}, f)
        except Exception as e:
            logger.error(f"Failed to save KIS token: {e}")

        if not self.api_key or self.api_key.startswith("YOUR_"):
            logger.warning("KIS API Key가 설정되지 않았습니다. 실제 호출은 실패합니다.")

    def authenticate(self) -> bool:
        """인증 토큰 발급 (/oauth2/tokenP)"""
        if not self.api_key or self.api_key.startswith("YOUR_"):
            return False
        
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.api_key,
            "appsecret": self.api_secret
        }
        headers = {"content-type": "application/json"}
        
        logger.info("Requesting KIS Access Token...")
        res = self.client.post(url, json=payload, headers=headers)
        
        if res and "access_token" in res:
            self.access_token = res["access_token"]
            self._save_token(self.access_token)
            logger.info("KIS Access Token acquired successfully.")
            return True
        
        logger.error(f"Failed to acquire KIS Access Token: {res}")
        return False

    def fetch_stock_price(self, symbol: str) -> Optional[Dict[str, Any]]:
        """주식 현재가 및 기본 정보 조회 (/uapi/domestic-stock/v1/quotations/inquire-price)"""
        if not self.access_token:
            if not self.authenticate():
                return None
                
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.api_key,
            "appsecret": self.api_secret,
            "tr_id": "FHKST01010100" # 주식현재가 시세 조회 ID
        }
        params = {
            "fid_cond_mrkt_div_code": "J", # 주식, 상장지수펀드 등
            "fid_input_iscd": symbol
        }
        
        logger.info(f"Fetching KIS stock price for {symbol}...")
        res = self.client.get(url, params=params, headers=headers)
        
        if res and res.get("rt_cd") == "0":
            return res.get("output")
        
        logger.error(f"Failed to fetch KIS stock price for {symbol}: {res}")
        return None

    def fetch_balance(self) -> Optional[Dict[str, Any]]:
        """계좌 잔고 및 예수금 현황 조회 (/uapi/domestic-stock/v1/trading/inquire-balance)"""
        if not self.access_token:
            if not self.authenticate():
                return None
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.api_key,
            "appsecret": self.api_secret,
            "tr_id": "TTTC8434R" # 실전 투자 주식잔고조회 TR ID
        }
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "AFHR_FLG": "N",
            "OVR_RE_REQS_GW_YN": "N"
        }
        
        logger.info("Fetching KIS account balance...")
        res = self.client.get(url, params=params, headers=headers)
        return res

    def place_order(self, symbol: str, quantity: int, price: int, side: str = "BUY") -> Optional[Dict[str, Any]]:
        """주식 주문 전송 (인터페이스 스켈레톤)"""
        logger.info(f"Placing {side} order for {symbol}: {quantity} shares @ {price} KRW")
        # 실제 구현 시 tr_id: TTTC0802U (매수), TTTC0801U (매도) 등 사용 필요
        return {"status": "success", "order_no": "DUMMY_12345"}
