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

    @staticmethod
    def _chunked(data: list, size: int):
        """리스트를 size 단위로 분할하는 제너레이터"""
        for i in range(0, len(data), size):
            yield data[i:i + size]

    def upsert_records(self, table_name: str, records: list, chunk_size: int = 1000, ignore_duplicates: bool = False):
        """
        1,000건 단위 청크 분할 업서트.
        기본값은 ignore_duplicates=False 로 설정하여 동일 PK 충돌 시 최신 값으로 갱신합니다.
        """
        if not records:
            return
        total = len(records)
        upserted = 0
        try:
            for chunk in self._chunked(records, chunk_size):
                self.client.table(table_name).upsert(chunk, ignore_duplicates=ignore_duplicates).execute()
                upserted += len(chunk)
            logger.info(f"[{table_name}] Successfully upserted {upserted}/{total} records.")
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

    def fetch_all(self, table_name: str, date_col: str, start_date: str, end_date: str, order_col: str = None, desc: bool = True) -> list:
        """
        범위 지정 및 자동 페이징(Chunking)을 통한 한도(1000건) 돌파 조회.
        Supabase의 기본 Max Rows 제한을 우회하기 위해 반복적인 쿼리를 수행합니다.
        
        Args:
            table_name: 데이터베이스 테이블 이름
            date_col: 날짜 기반 필터링을 수행할 컬럼명
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date: 종료 날짜 (YYYY-MM-DD)
            order_col: 정렬 기준 컬럼명
            desc: 내림차순 정렬 여부
        """
        all_data = []
        chunk_size = 1000
        max_chunks = 20  # 최대 20,000건 데이터 확보 가능
        
        for i in range(max_chunks):
            start_range = i * chunk_size
            end_range = start_range + chunk_size - 1
            
            try:
                query = self.client.table(table_name).select("*")\
                    .gte(date_col, start_date)\
                    .lte(date_col, end_date)
                    
                if order_col:
                    query = query.order(order_col, desc=desc)
                    
                query = query.range(start_range, end_range)
                res = query.execute()
                
                data = res.data if res and res.data else []
                all_data.extend(data)
                
                # 데이터가 chunk_size보다 적게 반환되면 마지막 페이지로 간주
                if len(data) < chunk_size:
                    break
            except Exception as e:
                logger.error(f"[{table_name}] 페이징 조회 실패 (Chunk {i+1}): {e}")
                break
                
        logger.info(f"[{table_name}] 전체 중복 제거 전 데이터 {len(all_data)}건 조회 완료 (Paging {i+1}회 수행)")
        return all_data
