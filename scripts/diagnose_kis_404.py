import asyncio
import aiohttp
import json
from src.utils.config_loader import load_config
from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.mapping import KIS_MAPPING

async def diagnose_kis_standard():
    print("--- Starting KIS API Official Standard Diagnosis ---")
    config = load_config()
    auth = KISAuthManager(config)
    token = await auth.get_access_token()
    
    # KIS 도메인 설정 (base.py와 동일 로직)
    is_real = config.get("kis", {}).get("is_real", True)
    domain = "openapi.koreainvestment.com" if is_real else "openvts.koreainvestment.com"
    port = 9443 if is_real else 7070
    base_url = f"https://{domain}:{port}"
    
    app_key = config["kis"]["app_key"]
    app_secret = config["kis"]["app_secret"]
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930", # 삼성전자
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_DATE_1": "20260401",
        "FID_INPUT_DATE_2": "20260413",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "1"
    }
    
    async with aiohttp.ClientSession() as session:
        for key, m in KIS_MAPPING.items():
            path = m["path"]
            tid = m["tr_id"]
            url = f"{base_url}{path}"
            
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": tid,
                "custtype": "P"
            }
            
            print(f"Testing [{key}]...")
            print(f"  URL: {url}")
            print(f"  TR_ID: {tid}")
            
            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    status = resp.status
                    print(f"  Status: {status}")
                    
                    if status == 200:
                        data = await resp.json()
                        rt_cd = data.get("rt_cd")
                        msg = data.get("msg1")
                        if rt_cd == '0':
                            print(f"  Result: SUCCESS (rt_cd={rt_cd})")
                        else:
                            print(f"  Result: BUSINESS_ERROR ({rt_cd}) - {msg}")
                    elif status == 404:
                        print("  Result: FAIL (404) - Endpoint still incorrect or not supported by this account type.")
                    else:
                        text = await resp.text()
                        print(f"  Result: FAIL ({status}) - {text[:100]}")
            except Exception as e:
                print(f"  Exception: {e}")
            print("-" * 40)

if __name__ == "__main__":
    asyncio.run(diagnose_kis_standard())
