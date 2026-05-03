import asyncio
import os
import sys
from collections import Counter

sys.path.append(os.getcwd())

from src.collectors.kis import KISAuthManager, KISDomesticStockCollector
from src.collectors.kis.base import KISBaseCollector
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.symbols import normalize_symbol_value


def _load_master_map(loader: SupabaseLoader):
    rows = loader.client.table("stocks_master").select("symbol, name, market, asset_type").execute().data or []
    return {normalize_symbol_value(row["symbol"]): row for row in rows if row.get("symbol")}


async def main():
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    master_map = _load_master_map(loader)
    auth_mgr = KISAuthManager(config)
    await auth_mgr.initialize()
    collector = KISDomesticStockCollector(config, auth_mgr, asyncio.Semaphore(2))
    try:
        for market_code in ["J", "K", "Q"]:
            rows = await collector.fetch_volume_rank(market_code=market_code)
            print(f"\n=== market_code={market_code} ===")
            print(f"row_count={len(rows or [])}")
            sample = []
            dist = Counter()
            for row in rows or []:
                symbol = normalize_symbol_value(row.get("mksc_shrn_iscd"))
                name = row.get("hts_kor_isnm")
                master = master_map.get(symbol, {})
                market = master.get("market") or "UNKNOWN"
                asset_type = master.get("asset_type") or "UNKNOWN"
                dist[(market, asset_type)] += 1
                if len(sample) < 10:
                    sample.append({"symbol": symbol, "name": name})
            print("first_10=", sample)
            print("master_market_distribution=", dict(sorted((f"{k[0]}/{k[1]}", v) for k, v in dist.items())))
    finally:
        await auth_mgr.shutdown()
        await KISBaseCollector.close_session()


if __name__ == "__main__":
    asyncio.run(main())
