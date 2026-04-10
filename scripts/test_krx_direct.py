from datetime import date
from src.collectors.krx_collector import KRXCollector

def test_krx_direct():
    collector = KRXCollector()
    target_date = date(2026, 4, 8) # Wednesday
    symbol = "005930"
    
    print(f"Testing KRX Direct API for {symbol} on {target_date}...")
    
    # Test OHLCV
    ohlcv = collector.fetch_daily_ohlcv(symbol, target_date)
    print(f"\nOHLCV Data: {ohlcv}")
    
    # Test Supply
    supply = collector.fetch_daily_investor_supply(symbol, target_date)
    print(f"\nSupply Data: {supply}")

if __name__ == "__main__":
    test_krx_direct()
