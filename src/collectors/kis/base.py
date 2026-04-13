import asyncio
import aiohttp
import time
from typing import Dict, Any, List, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.utils.logger import get_logger
from .auth import KISAuthManager

logger = get_logger(__name__)

class RateLimiter:
    """초당 요청 건수(TPS)를 엄격히 제한하는 헬퍼 클래스"""
    def __init__(self, tps: float):
        self.interval = 1.0 / tps
        self.last_call = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            wait_time = self.last_call + self.interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.last_call = asyncio.get_event_loop().time()

class KISBaseCollector:
    """
    고안정성 KIS 수집기 베이스 클래스.
    세션 풀링, 지수적 백오프 재시도, TPS 쓰로틀링을 지원합니다.
    """
    _session: Optional[aiohttp.ClientSession] = None

    def __init__(self, config: Dict[str, Any], auth_manager: KISAuthManager, 
                 semaphore: asyncio.Semaphore, tps_limiter: Optional[RateLimiter] = None):
        self.config = config
        self.auth = auth_manager
        self.semaphore = semaphore
        self.tps_limiter = tps_limiter or RateLimiter(1.8) # 안전하게 1.8 TPS 설정
        self.app_key = config.get("kis", {}).get("app_key", "")
        self.app_secret = config.get("kis", {}).get("app_secret", "")
        
        # 기본 도메인 설정 (실전: REAL, 모의: VIRTUAL)
        is_real = config.get("kis", {}).get("is_real", True)
        domain = "openapi.koreainvestment.com" if is_real else "openvts.koreainvestment.com"
        port = 9443 if is_real else 7070
        self.base_url = f"https://{domain}:{port}"
        
        from src.loaders.supabase_loader import SupabaseLoader
        self.db_loader = SupabaseLoader(
            config.get("supabase", {}).get("url", ""),
            config.get("supabase", {}).get("service_role_key", "")
        )

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        """글로벌 세션 반환 (커넥션 풀링)"""
        if cls._session is None or cls._session.closed:
            # 타임아웃 및 커넥션 풀 설정
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=60)
            cls._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return cls._session

    @classmethod
    async def close_session(cls):
        """세션 종료 (어플리케이션 종료 시 호출 권장)"""
        if cls._session and not cls._session.closed:
            await cls._session.close()

    async def _get_headers(self, tr_id: str) -> Dict[str, str]:
        token = await self.auth.get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P"
        }

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=15),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError, ConnectionError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Retrying KIS API request (Attempt {retry_state.attempt_number}) after error: {retry_state.outcome.exception()}"
        )
    )
    async def _request(self, method: str, path: str, tr_id: str, params: Optional[Dict[str, Any]] = None, 
                       json_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Rate limit 및 TPS 소모를 관리하며 안정적으로 API를 호출합니다.
        """
        url = f"{self.base_url.strip()}{path.strip()}"
        headers = await self._get_headers(tr_id)
        session = await self.get_session()
        
        async with self.semaphore:
            await self.tps_limiter.wait() # 정밀 TPS 제어
            
            # 명시적 메서드 처리
            if method.upper() == "GET":
                request_func = session.get
                request_args = {"params": params}
            else:
                request_func = session.post
                request_args = {"json": json_data}
            
            async with request_func(url, headers=headers, **request_args) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # KIS 비즈니스 로직 에러 처리 (API 성공 status지만 내부 응답이 에러인 경우)
                    if data.get("rt_cd") != '0':
                        msg = data.get('msg1', 'Unknown KIS Error')
                        logger.error(f"KIS Business Logic Error [{tr_id}]: {msg}")
                        # 특정 에러 코드(예: 시스템 점검 등)는 재시도 필요할 수 있음
                        return None
                    return data
                elif resp.status == 429:
                    logger.warning(f"Rate limit hit (429) for {tr_id}. Waiting for exponential backoff...")
                    raise aiohttp.ClientError("Rate limited")
                elif resp.status >= 500:
                    logger.error(f"KIS Server Error [{resp.status}] for {tr_id}")
                    raise aiohttp.ClientError(f"Server error: {resp.status}")
                elif resp.status == 404:
                    logger.warning(f"KIS API Resource Not Found (404) for {tr_id} at {url}. Skipping this endpoint.")
                    return None
                else:
                    text = await resp.text()
                    logger.error(f"KIS API Error [{resp.status}] for {tr_id} at {url}: {text}")
                    return None

    async def upsert_records(self, table_name: str, records: List[Dict[str, Any]]):
        """데이터 적재 (Sync 로더를 ThreadPool에서 비동기로 실행)"""
        if not records: return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.db_loader.upsert_records, table_name, records)
        except Exception as e:
            logger.error(f"Supabase upsert failure in {table_name}: {e}")
