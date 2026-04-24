import json
import os

from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config


def load_universe():
    """Load universe from stocks_master first, then fall back to config/stock_universe.json."""
    try:
        config = load_config()
        supabase = config.get("supabase", {})
        if supabase.get("url") and supabase.get("service_role_key"):
            loader = SupabaseLoader(url=supabase["url"], key=supabase["service_role_key"])
            res = (
                loader.client.table("stocks_master")
                .select("symbol, name, market")
                .eq("is_active", True)
                .order("symbol")
                .execute()
            )
            if res.data:
                return res.data
    except Exception:
        pass

    path = "config/stock_universe.json"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
