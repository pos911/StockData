import asyncio
import aiohttp
import json
from src.utils.config_loader import load_config
from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.mapping import KIS_MAPPING

async def capture_evidence_logs():
    config = load_config()
    auth = KISAuthManager(config)
    token = await auth.get_access_token()
    
    # Official evidence check for 005930
    is_real = config.get("kis", {}).get("is_real", True)
    domain = "openapi.koreainvestment.com" if is_real else "openvts.koreainvestment.com"
    port = 9443 if is_real else 7070
    base_url = f"https://{domain}:{port}"
    
    app_key = config["kis"]["app_key"]
    app_secret = config["kis"]["app_secret"]
    
    # We will test the critical corrected endpoints
    targets = {
        "Short Selling": KIS_MAPPING["short_selling"],
        "Stability Ratio": KIS_MAPPING["stability_ratio"],
        "Growth Ratio": KIS_MAPPING["growth_ratio"]
    }
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
        "FID_DIV_CLS_CODE": "0",
        "FID_INPUT_DATE_1": "20260401",
        "FID_INPUT_DATE_2": "20260413",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "1"
    }
    
    async with aiohttp.ClientSession() as session:
        for name, m in targets.items():
            url = f"{base_url}{m['path']}"
            tid = m["tr_id"]
            
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": tid,
                "custtype": "P"
            }
            
            async with session.get(url, headers=headers, params=params) as resp:
                status = resp.status
                data = await resp.json() if status == 200 else {"error": await resp.text()}
                
                print(f"[Evidence Log: {name}]")
                print(f"URL: {url}")
                print(f"TR_ID: {tid}")
                print(f"HTTP Status: {status}")
                print(f"rt_cd: {data.get('rt_cd')}")
                print(f"msg_cd: {data.get('msg_cd')}")
                print(f"msg1: {data.get('msg1')}")
                print("-" * 50)

if __name__ == "__main__":
    asyncio.run(capture_evidence_logs())
