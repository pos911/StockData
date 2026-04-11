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

    def upsert_records(self, table_name: str, records: list):
        if not records: return
        try:
            # 데이터가 이미 존재할 경우 무시(ignore) 하도록 옵션 추가
            res = self.client.table(table_name).upsert(records, ignore_duplicates=True).execute()
            logger.info(f"[{table_name}] Successfully upserted {len(records)} records.")
        except Exception as e:
            logger.error(f"Failed to upsert records into {table_name}: {e}")

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
