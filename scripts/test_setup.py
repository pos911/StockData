import asyncio
from datetime import date
from src.jobs.run_daily_stock_pipeline import run_pipeline
from src.utils.time_utils import get_current_kst
from src.utils.logger import get_logger

logger = get_logger(__name__)

async def run_test():
    target_dt = get_current_kst().date()
    # We'll monkeypatch the universe loader or just pass a custom one if possible
    # But since it's a test, I'll just temporarily modify a copy of the logic
    
    # Actually, let's just run the macro pipeline and check if it worked first.
    print("Testing Daily Stock Pipeline for a small subset...")
    
    # For testing, we can use a small subset by importing run_pipeline and modifying what it iterates over
    # But currently run_pipeline is monolithic. 
    # I'll just run it as-is for now and manually stop it or let it run if it's fast enough.
    # Alternatively, I'll create a very small universe file.
    
    import json
    test_universe = [
        {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "enabled": True},
        {"symbol": "000660", "name": "SK하이닉스", "market": "KOSPI", "enabled": True}
    ]
    with open("config/stock_universe_test.json", "w", encoding="utf-8") as f:
        json.dump(test_universe, f)
        
    # I'll modify run_daily_stock_pipeline.py slightly to accept a universe file or I'll just use the test script to call the logic.
    # Actually, I'll just run the actual pipelines.

if __name__ == "__main__":
    asyncio.run(run_test())
