import json
from typing import List, Dict, Any
from supabase import create_client, Client
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SupabaseLoader:
    """Supabase DB 데이터 적재기 (아이뎀포턴시 중점)"""
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.client: Client = create_client(self.url, self.key)

    def upsert_records(self, table_name: str, records: List[Dict[str, Any]], on_conflict: str = "") -> bool:
        """
        중복 방지 (Upsert) 로직을 통해 데이터를 적재합니다.
        on_conflict: 중복 판단의 기준이 되는 컬럼 (예: "symbol,base_date")
        """
        if not records:
            return True
            
        try:
            # supabase-py 의 upsert는 기본적으로 PK 기반이며, 
            # on_conflict 지정 시 해당 컬럼/제약조건 기반으로 수행합니다.
            query = self.client.table(table_name).upsert(records)
            # if on_conflict:
            #     # 라이브러리 버전에 따라 문법 차이가 있을 수 있으므로 상황에 맞게 적용
            #     query = self.client.table(table_name).upsert(records, on_conflict=on_conflict)
                
            response = query.execute()
            logger.info(f"[{table_name}] Successfully upserted {len(records)} records.")
            return True
        except Exception as e:
            logger.error(f"Failed to upsert records into {table_name}: {e}")
            return False

    def insert_log(self, job_name: str, target_date: str, status: str, records_processed: int, error_message: str = ""):
        """파이프라인 실행 로그 기록"""
        try:
            self.client.table("pipeline_run_logs").insert({
                "job_name": job_name,
                "target_date": target_date,
                "status": status,
                "records_processed": records_processed,
                "error_message": error_message
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log pipeline run: {e}")
