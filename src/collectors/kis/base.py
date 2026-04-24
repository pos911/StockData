import asyncio
from functools import partial
from typing import Dict, Any, List, Optional

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.utils.logger import get_logger
from .auth import KISAuthManager

logger = get_logger(__name__)


class RateLimiter:
    """Rate-limit helper for KIS API requests."""

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
    Shared base collector for KIS API access.
    Keeps one shared aiohttp session and centralizes retries/rate limits.
    """

    _session: Optional[aiohttp.ClientSession] = None

    def __init__(
        self,
        config: Dict[str, Any],
        auth_manager: KISAuthManager,
        semaphore: asyncio.Semaphore,
        tps_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config
        self.auth = auth_manager
        self.semaphore = semaphore
        self.tps_limiter = tps_limiter or RateLimiter(1.8)
        self.app_key = config.get("kis", {}).get("app_key", "")
        self.app_secret = config.get("kis", {}).get("app_secret", "")

        is_real = config.get("kis", {}).get("is_real", True)
        domain = "openapi.koreainvestment.com" if is_real else "openvts.koreainvestment.com"
        port = 9443 if is_real else 7070
        self.base_url = f"https://{domain}:{port}"

        from src.loaders.supabase_loader import SupabaseLoader

        self.db_loader = SupabaseLoader(
            config.get("supabase", {}).get("url", ""),
            config.get("supabase", {}).get("service_role_key", ""),
        )

    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        """Return one shared aiohttp session for all KIS collectors."""
        if KISBaseCollector._session is None or KISBaseCollector._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=60)
            KISBaseCollector._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return KISBaseCollector._session

    @classmethod
    async def close_session(cls):
        """Close the shared aiohttp session."""
        if KISBaseCollector._session and not KISBaseCollector._session.closed:
            await KISBaseCollector._session.close()
            KISBaseCollector._session = None

    async def _get_headers(self, tr_id: str) -> Dict[str, str]:
        token = await self.auth.get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=15),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError, ConnectionError)),
        before_sleep=lambda retry_state: logger.warning(
            f"Retrying KIS API request (Attempt {retry_state.attempt_number}) after error: {retry_state.outcome.exception()}"
        ),
    )
    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute one KIS API request with rate limiting and retries."""
        url = f"{self.base_url.strip()}{path.strip()}"
        headers = await self._get_headers(tr_id)
        session = await self.get_session()

        async with self.semaphore:
            await self.tps_limiter.wait()

            if method.upper() == "GET":
                request_func = session.get
                request_args = {"params": params}
            else:
                request_func = session.post
                request_args = {"json": json_data}

            async with request_func(url, headers=headers, **request_args) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("rt_cd") != "0":
                        msg = data.get("msg1", "Unknown KIS Error")
                        logger.error(f"KIS Business Logic Error [{tr_id}]: {msg}")
                        return None
                    return data
                if resp.status == 429:
                    logger.warning(f"Rate limit hit (429) for {tr_id}. Waiting for exponential backoff...")
                    raise aiohttp.ClientError("Rate limited")
                if resp.status >= 500:
                    logger.error(f"KIS Server Error [{resp.status}] for {tr_id}")
                    raise aiohttp.ClientError(f"Server error: {resp.status}")
                if resp.status == 404:
                    logger.warning(f"KIS API Resource Not Found (404) for {tr_id} at {url}. Skipping this endpoint.")
                    return None

                text = await resp.text()
                logger.error(f"KIS API Error [{resp.status}] for {tr_id} at {url}: {text}")
                return None

    async def upsert_records(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        ignore_duplicates: bool = False,
        on_conflict: Optional[str] = None,
        raise_on_error: bool = False,
    ):
        """Run Supabase upserts in a thread executor from async collectors."""
        if not records:
            return
        try:
            loop = asyncio.get_running_loop()
            upsert = partial(
                self.db_loader.upsert_records,
                table_name,
                records,
                ignore_duplicates=ignore_duplicates,
                on_conflict=on_conflict,
                raise_on_error=raise_on_error,
            )
            return await loop.run_in_executor(None, upsert)
        except Exception as exc:
            logger.error(f"Supabase upsert failure in {table_name}: {exc}")
            if raise_on_error:
                raise
            return False
