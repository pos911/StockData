import json
from supabase import create_client, Client
from src.utils.logger import get_logger

logger = get_logger(__name__)

def check_data_status():
    try:
        with open("config/api_keys.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        
        url = config["supabase"]["url"]
        key = config["supabase"]["service_role_key"]
        supabase: Client = create_client(url, key)

        # 1. 매크로 데이터 요약 (최신 데이터 위주로 확인)
        macro_summary = supabase.table("normalized_macro_series") \
            .select("series_id, base_date") \
            .order("base_date", desc=True) \
            .limit(2000) \
            .execute()
        data = macro_summary.data
        
        if not data:
            logger.warning("No macro data found in normalized_macro_series.")
        else:
            stats = {}
            for row in data:
                sid = row['series_id']
                bdate = row['base_date']
                if sid not in stats:
                    stats[sid] = {"count": 0, "min_date": bdate, "max_date": bdate}
                stats[sid]["count"] += 1
                stats[sid]["min_date"] = min(stats[sid]["min_date"], bdate)
                stats[sid]["max_date"] = max(stats[sid]["max_date"], bdate)
            
            logger.info("--- Macro Data Statistics ---")
            for sid, sinfo in stats.items():
                logger.info(f"Series: {sid} | Count: {sinfo['count']} | Range: {sinfo['min_date']} ~ {sinfo['max_date']}")

        # 2. 파이프라인 로그 확인
        logs = supabase.table("pipeline_run_logs").select("*").order("start_time", desc=True).limit(5).execute()
        logger.info("--- Recent Pipeline Logs ---")
        for log in logs.data:
            logger.info(f"Job: {log['job_name']} | Status: {log['status']} | Processed: {log['records_processed']} | Date: {log['target_date']}")

    except Exception as e:
        logger.error(f"Error checking data status: {e}")

if __name__ == "__main__":
    check_data_status()
