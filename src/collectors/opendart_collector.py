from typing import Dict, Any, Optional, List
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class OpenDartCollector:
    """금융감독원 OpenDart 수집기"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = HttpClient()
        self.base_url = "https://opendart.fss.or.kr/api"
        self.cache_dir = "data"
        self.corp_code_file = f"{self.cache_dir}/corp_codes.json"

        if not self.api_key or self.api_key.startswith("YOUR_"):
            logger.warning("OpenDart API Key가 설정되지 않았습니다.")

    def fetch_corp_code_map(self) -> Dict[str, str]:
        """전종목 stock_code <-> corp_code 매핑 테이블 생성 (캐싱 지원)"""
        import os
        import zipfile
        import io
        import xml.etree.ElementTree as ET
        import json

        if os.path.exists(self.corp_code_file):
            with open(self.corp_code_file, "r", encoding="utf-8") as f:
                return json.load(f)

        logger.info("Downloading and parsing OpenDart corp codes...")
        url = f"{self.base_url}/corpCode.xml"
        params = {"crtfc_key": self.api_key}
        
        # requests를 직접 사용하여 바이너리 데이터 수집
        import requests
        res = requests.get(url, params=params)
        if res.status_code != 200:
            logger.error("Failed to download corpCode zip from OpenDart")
            return {}

        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            xml_data = z.read("CORPCODE.xml")
            root = ET.fromstring(xml_data)
            
            mapping = {}
            for list_node in root.findall("list"):
                stock_code = list_node.findtext("stock_code").strip()
                corp_code = list_node.findtext("corp_code").strip()
                if stock_code: # 상장사만 필터링
                    mapping[stock_code] = corp_code
            
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.corp_code_file, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=2)
            
            return mapping

    def fetch_daily_disclosures(self, corp_code: str, target_date: str) -> Optional[List[Dict[str, Any]]]:
        """특정 일자의 종목별 공시 내역 수집"""
        if not self.api_key or self.api_key.startswith("YOUR_"):
            return None
            
        url = f"{self.base_url}/list.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": target_date.replace("-", ""),
            "end_de": target_date.replace("-", ""),
            "page_no": "1",
            "page_count": "100"
        }
        res = self.client.get(url, params=params)
        if res and res.get("status") == "000":
            return res.get("list")
        return []
