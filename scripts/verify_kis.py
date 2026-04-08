import json
from src.collectors.kis_collector import KISCollector
from src.utils.logger import get_logger

logger = get_logger(__name__)

def test_kis():
    try:
        with open("config/api_keys.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        
        kis_config = config.get("kis", {})
        collector = KISCollector(
            api_key=kis_config.get("app_key", ""),
            api_secret=kis_config.get("app_secret", ""),
            account_no=kis_config.get("account_no", "")
        )
        
        logger.info("Testing KIS Authentication...")
        if collector.authenticate():
            logger.info("Authentication Success!")
            price_info = collector.fetch_stock_price("005930") # 삼성전자
            if price_info:
                logger.info(f"Successfully fetched price info: {price_info.get('stck_prpr')} KRW")
            else:
                logger.error("Failed to fetch price info.")
        else:
            logger.error("Authentication Failed.")
            
    except Exception as e:
        logger.error(f"Test failed with error: {e}")

if __name__ == "__main__":
    test_kis()
