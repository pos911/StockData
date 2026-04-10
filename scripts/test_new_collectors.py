from datetime import date
from src.collectors.krx_collector import KRXCollector

def test_new_collectors():
    collector = KRXCollector()
    # Wednesday, April 8, 2026 (Recent date that should be on Naver page 1)
    target_date = date(2026, 4, 8) 
    symbol = "005930"
    
    print(f"Testing New Collectors for {symbol} on {target_date}...")
    
    # Test OHLCV via FinanceDataReader
    ohlcv = collector.fetch_daily_ohlcv(symbol, target_date)
    print(f"\nOHLCV Data (FDR): {ohlcv}")
    
    # Test Supply via Naver Finance
    supply = collector.fetch_daily_investor_supply(symbol, target_date)
    print(f"\nSupply Data (Naver): {supply}")

if __name__ == "__main__":
    test_new_collectors()
