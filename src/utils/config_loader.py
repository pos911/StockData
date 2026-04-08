import os
import json
from typing import Dict, Any

def load_config() -> Dict[str, Any]:
    """
    설정을 로드합니다. config/api_keys.json을 우선적으로 읽으며, 
    파일이 없거나 환경변수가 설정된 경우 환경변수에서 개별 값을 읽어옵니다.
    """
    config_path = "config/api_keys.json"
    config = {}

    # 1. JSON 파일 로드 시도
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass

    # 2. 개별 환경변수 Fallback (GitHub Actions 등 배포 환경용)
    if not config.get("kis"):
        config["kis"] = {
            "app_key": os.getenv("KIS_APP_KEY"),
            "app_secret": os.getenv("KIS_APP_SECRET"),
            "account_no": os.getenv("KIS_ACCOUNT_NO"),
            "product_code": os.getenv("KIS_PRODUCT_CODE", "01")
        }
    
    if not config.get("krx"):
        config["krx"] = {"auth_key": os.getenv("KRX_AUTH_KEY")}
        
    if not config.get("opendart"):
        config["opendart"] = {"api_key": os.getenv("OPENDART_API_KEY")}

    if not config.get("fred"):
        config["fred"] = {"api_key": os.getenv("FRED_API_KEY")}

    if not config.get("naver"):
        config["naver"] = {
            "client_id": os.getenv("NAVER_CLIENT_ID"),
            "client_secret": os.getenv("NAVER_CLIENT_SECRET")
        }

    if not config.get("supabase"):
        config["supabase"] = {
            "url": os.getenv("SUPABASE_URL"),
            "publishable_key": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "service_role_key": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            "connection_string": os.getenv("SUPABASE_CONNECTION_STRING")
        }

    return config
