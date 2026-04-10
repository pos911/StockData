from typing import Dict, Any, List
from datetime import datetime

class StockNormalizer:
    """
    다양한 소스의 주식 (KRX 등) 원본 데이터를
    normalized_stock_prices_daily 스키마 형식으로 정규화합니다.
    """
    @staticmethod
    def normalize_krx_daily(raw_data: Dict[str, Any], available_at: datetime) -> Dict[str, Any]:
        """
        KRX 수집기가 반환한 raw_data를 정규화합니다.
        실제 운영 시에는 포맷팅 방어 코드가 들어가야 합니다.
        """
        return {
            "symbol": raw_data.get("symbol"),
            "base_date": raw_data.get("base_date"),
            "open_price": float(raw_data.get("open", 0)),
            "high_price": float(raw_data.get("high", 0)),
            "low_price": float(raw_data.get("low", 0)),
            "close_price": float(raw_data.get("close", 0)),
            "volume": float(raw_data.get("volume", 0)),
            "trading_value": float(raw_data.get("trading_value", 0)),
            "market_cap": float(raw_data.get("market_cap", 0)),
            "outstanding_shares": float(raw_data.get("outstanding_shares", 0)),
            "available_at": available_at.isoformat()
        }

    @staticmethod
    def normalize_kis_price(raw_data: Dict[str, Any], available_at: datetime) -> Dict[str, Any]:
        """KIS 주가 데이터를 normalized_stock_prices_daily 형식으로 변환"""
        # API 응답 구조에 따라 'output' 필드 내부 확인
        data = raw_data.get("output", raw_data)
        
        return {
            "symbol": data.get("stck_shrn_iscd"),
            "base_date": available_at.strftime("%Y-%m-%d"),
            "open_price": float(data.get("stck_oprc", 0)),
            "high_price": float(data.get("stck_hgpr", 0)),
            "low_price": float(data.get("stck_lwpr", 0)),
            "close_price": float(data.get("stck_prpr", 0)),
            "volume": float(data.get("acml_vol", 0)),
            "trading_value": float(data.get("acml_tr_pbmn", 0)),
            "available_at": available_at.isoformat()
        }

    @staticmethod
    def normalize_kis_history(raw_data: List[Dict[str, Any]], symbol: str, available_at: datetime) -> List[Dict[str, Any]]:
        """KIS 기간별 시세 응답(List)을 정규화"""
        normalized = []
        for item in raw_data:
            base_date = item.get("stck_bsop_date")
            if base_date:
                # YYYYMMDD -> YYYY-MM-DD
                formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}"
                normalized.append({
                    "symbol": symbol,
                    "base_date": formatted_date,
                    "open_price": float(item.get("stck_oprc", 0)),
                    "high_price": float(item.get("stck_hgpr", 0)),
                    "low_price": float(item.get("stck_lwpr", 0)),
                    "close_price": float(item.get("stck_clpr", 0)),
                    "volume": float(item.get("acml_vol", 0)),
                    "trading_value": float(item.get("acml_tr_pbmn", 0)),
                    "available_at": available_at.isoformat()
                })
        return normalized

    @staticmethod
    def normalize_stock_master(symbol: str, name: str, market: str) -> Dict[str, Any]:
        """stocks_master 테이블 형식으로 변환"""
        return {
            "symbol": symbol,
            "name": name,
            "market": market,
            "is_active": True,
            "updated_at": datetime.now().isoformat()
        }
