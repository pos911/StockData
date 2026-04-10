import json
from src.collectors.kis_collector import KISCollector
from src.utils.config_loader import load_config

if __name__ == "__main__":
    config = load_config()
    print("Testing KIS Collector Token Cache...")
    collector = KISCollector(config)
    
    # First call - Should hit DB or KIS API directly depending on DB setup
    print("\n[Call 1]")
    token1 = collector._get_token()
    print("Fetched Token:", token1[:10] + "...")
    
    # Second call - Should hit memory cache
    print("\n[Call 2]")
    token2 = collector._get_token()
    print("Fetched Token:", token2[:10] + "...")
    
    print("\nDone testing.")
