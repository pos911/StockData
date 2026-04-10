import json
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config

def check_new_tables():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    tables = [
        "normalized_stock_prices_daily",
        "normalized_global_macro_daily",
        "normalized_stock_supply_daily",
        "normalized_derivatives_daily",
        "normalized_stock_events_daily",
        "feature_store_daily"
    ]
    
    print("--- Table Status ---")
    for table in tables:
        try:
            # Using Supabase count feature
            res = loader.client.table(table).select("*", count='exact').limit(1).execute()
            count = res.count
            print(f"Table: {table:32} | Count: {count}")
        except Exception as e:
            print(f"Table: {table:32} | Error: {e}")

if __name__ == "__main__":
    check_new_tables()
