import asyncio
import time
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import dateutil.parser
from src.utils.logger import get_logger

logger = get_logger(__name__)

class KISAuthManager:
    """
    KIS API 인증을 전담하는 싱글톤 클래스.
    OAuth 토큰의 발급, 저장(Supabase), 갱신을 자동으로 관리하며
    백그라운드 갱신 태스크를 통해 무중단 수집을 지원합니다.
    """
    _instance: Optional['KISAuthManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls, config: Dict[str, Any]):
        if cls._instance is None:
            cls._instance = super(KISAuthManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: Dict[str, Any]):
        if self._initialized:
            return
            
        self.app_key = config.get("kis", {}).get("app_key", "")
        self.app_secret = config.get("kis", {}).get("app_secret", "")
        self.base_url = "https://openapi.koreainvestment.com:9443"
        
        supa_url = config.get("supabase", {}).get("url", "")
        supa_key = config.get("supabase", {}).get("service_role_key", "")
        
        self.db_client = None
        if supa_url and supa_key:
            try:
                from supabase import create_client
                self.db_client = create_client(supa_url, supa_key)
            except ImportError:
                logger.warning("Supabase client not installed. Persistence disabled.")

        self._access_token = None
        self._token_expires_at = 0
        self._refresh_task = None
        self._initialized = True

    async def initialize(self):
        """초기 토큰 로드 및 백그라운드 갱신 태스크 시작"""
        await self.get_access_token()
        if not self._refresh_task:
            self._refresh_task = asyncio.create_task(self._background_token_refresher())
            logger.info("KIS Token background refresher started.")

    async def get_access_token(self) -> str:
        """유효한 토큰 반환 (없으면 새로 발급)"""
        async with self._lock:
            # 1. 메모리 확인 (만료 5분 전까지 인정)
            if self._access_token and self._token_expires_at > time.time() + 300:
                return self._access_token
                
            # 2. DB 확인
            if self.db_client:
                try:
                    res = self.db_client.table("api_tokens").select("*").eq("service_name", "kis").execute()
                    if res.data:
                        row = res.data[0]
                        dt_expires = dateutil.parser.isoparse(row.get("expires_at", ""))
                        if dt_expires > datetime.now(timezone.utc):
                            self._access_token = row["token_value"]
                            self._token_expires_at = dt_expires.timestamp()
                            return self._access_token
                except Exception as e:
                    logger.warning(f"Failed to load token from Supabase: {e}")

            # 3. 신규 발급
            return await self._refresh_token()

    async def _refresh_token(self) -> str:
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"KIS Token Refresh Failed: {text}")
                    raise Exception(f"Auth Error: {text}")
                
                data = await resp.json()
                self._access_token = data["access_token"]
                # 24시간 중 23시간만 유효한 것으로 간주하여 안전하게 관리
                self._token_expires_at = time.time() + (23 * 3600)
                
                # DB 저장
                if self.db_client:
                    try:
                        expires_iso = datetime.fromtimestamp(self._token_expires_at, tz=timezone.utc).isoformat()
                        self.db_client.table("api_tokens").upsert({
                            "service_name": "kis",
                            "token_value": self._access_token,
                            "expires_at": expires_iso
                        }).execute()
                    except Exception as e:
                        logger.warning(f"Failed to persist token: {e}")
                        
                return self._access_token

    async def _background_token_refresher(self):
        """주기적으로 토큰 상태를 점검하여 만료 전 사전 갱신"""
        while True:
            try:
                # 1시간마다 체크
                await asyncio.sleep(3600)
                # 만료 30분 전이면 강제 갱신
                if self._token_expires_at < time.time() + 1800:
                    logger.info("Renewing KIS token in background...")
                    await self._refresh_token()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background token refresher error: {e}")
                await asyncio.sleep(300) # 에러 발생 시 5분 대기 후 재시도
