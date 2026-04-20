import json
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from src.utils.logger import get_logger

logger = get_logger(__name__)

TABLE_CONFLICT_KEYS = {
    "raw_stock_prices_daily": ["source", "symbol", "base_date"],
    "raw_stock_supply_daily": ["source", "symbol", "base_date"],
    "raw_macro_series": ["source", "series_id", "base_date"],
    "normalized_stock_prices_daily": ["symbol", "base_date"],
    "normalized_stock_supply_daily": ["symbol", "base_date"],
    "normalized_stock_short_selling": ["symbol", "base_date"],
    "normalized_stock_fundamentals": ["symbol", "base_date"],
    "normalized_stock_fundamentals_ratios": ["symbol", "base_date"],
    "normalized_stock_events_daily": ["symbol", "base_date", "event_type"],
    "normalized_macro_series": ["series_id", "base_date"],
    "normalized_global_macro_daily": ["base_date"],
    "market_breadth_daily": ["base_date"],
    "normalized_derivatives_daily": ["base_date"],
    "feature_store_daily": ["symbol", "base_date", "feature_name"],
    "stocks_master": ["symbol"],
    "macro_series_master": ["series_id"],
}

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

    @staticmethod
    def _deduplicate(records: list, key_fields: Optional[List[str]]) -> list:
        if not key_fields:
            return records

        deduped = {}
        passthrough = []
        for record in records:
            if any(record.get(field) in (None, "") for field in key_fields):
                passthrough.append(record)
                continue
            key = tuple(record.get(field) for field in key_fields)
            deduped[key] = record
        return list(deduped.values()) + passthrough

    def upsert_records(
        self,
        table_name: str,
        records: list,
        chunk_size: int = 1000,
        ignore_duplicates: bool = False,
        on_conflict: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> bool:
        """
        1,000건 단위 청크 분할 업서트.
        기본값은 ignore_duplicates=False 로 설정하여 동일 PK 충돌 시 최신 값으로 갱신합니다.
        """
        if not records:
            return True

        conflict_keys = TABLE_CONFLICT_KEYS.get(table_name)
        if on_conflict is None and conflict_keys:
            on_conflict = ",".join(conflict_keys)

        records = self._deduplicate(records, conflict_keys)
        total = len(records)
        upserted = 0
        try:
            for chunk in self._chunked(records, chunk_size):
                upsert_kwargs = {"ignore_duplicates": ignore_duplicates}
                if on_conflict:
                    upsert_kwargs["on_conflict"] = on_conflict
                query = self.client.table(table_name).upsert(chunk, **upsert_kwargs)
                query.execute()
                upserted += len(chunk)
            logger.info(f"[{table_name}] Successfully upserted {upserted}/{total} records.")
            return True
        except Exception as e:
            logger.error(f"Failed to upsert records into {table_name}: {e}")
            if raise_on_error:
                raise
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
