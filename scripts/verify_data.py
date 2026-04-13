import json
from datetime import date, timedelta
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.logger import get_logger

logger = get_logger(__name__)

def verify_data():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    tables = [
        "normalized_stock_prices_daily",
        "normalized_stock_supply_daily",
        "normalized_stock_short_selling",
        "normalized_stock_fundamentals",
        "normalized_stock_fundamentals_ratios",
        "normalized_stock_events_daily",
        "normalized_macro_series",
        "normalized_global_macro_daily",
        "market_breadth_daily",
        "stocks_master",
        "macro_series_master"
    ]
    
    print("\n=== Data Verification Report ===\n")
    print(f"{'Table Name':<35} | {'Count':<8} | {'Latest Date':<12}")
    print("-" * 65)
    
    for table in tables:
        try:
            # Get total count
            res_count = loader.client.table(table).select("*", count="exact").limit(1).execute()
            count = res_count.count
            
            # Get latest date
            latest_date = "N/A"
            if count > 0:
                # 마스터 테이블과 일반 테이블의 날짜 컬럼 구분
                if "master" in table:
                    order_col = "updated_at"
                elif "global" in table or "breadth" in table or "derivatives" in table:
                    order_col = "base_date"
                else:
                    order_col = "base_date"
                
                try:
                    res_latest = loader.client.table(table).select(order_col).order(order_col, descending=True).limit(1).execute()
                    if res_latest.data:
                        val = res_latest.data[0].get(order_col, "N/A")
                        latest_date = str(val)[:10] # YYYY-MM-DD
                except Exception:
                    latest_date = "Check Col"
            
            print(f"{table:<35} | {count:<8} | {latest_date:<12}")
        except Exception as e:
            print(f"{table:<35} | ERROR    | {str(e)[:30]}...")

if __name__ == "__main__":
    verify_data()
