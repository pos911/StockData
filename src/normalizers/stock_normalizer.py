from typing import Dict, Any, List
from datetime import datetime


def _parse_float_nullable(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


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
            "open_price": _parse_float_nullable(raw_data.get("open")),
            "high_price": _parse_float_nullable(raw_data.get("high")),
            "low_price": _parse_float_nullable(raw_data.get("low")),
            "close_price": _parse_float_nullable(raw_data.get("close")),
            "volume": _parse_float_nullable(raw_data.get("volume")),
            "trading_value": _parse_float_nullable(raw_data.get("trading_value")),
            "market_cap": _parse_float_nullable(raw_data.get("market_cap")),
            "outstanding_shares": _parse_float_nullable(raw_data.get("outstanding_shares")),
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
            "open_price": _parse_float_nullable(data.get("stck_oprc")),
            "high_price": _parse_float_nullable(data.get("stck_hgpr")),
            "low_price": _parse_float_nullable(data.get("stck_lwpr")),
            "close_price": _parse_float_nullable(data.get("stck_prpr")),
            "volume": _parse_float_nullable(data.get("acml_vol")),
            "trading_value": _parse_float_nullable(data.get("acml_tr_pbmn")),
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
                    "open_price": _parse_float_nullable(item.get("stck_oprc")),
                    "high_price": _parse_float_nullable(item.get("stck_hgpr")),
                    "low_price": _parse_float_nullable(item.get("stck_lwpr")),
                    "close_price": _parse_float_nullable(item.get("stck_clpr")),
                    "volume": _parse_float_nullable(item.get("acml_vol")),
                    "trading_value": _parse_float_nullable(item.get("acml_tr_pbmn")),
                    "available_at": available_at.isoformat()
                })
        return normalized

    @staticmethod
    def normalize_stock_master(symbol: str, name: str, market: str, asset_type: str = "STOCK") -> Dict[str, Any]:
        """stocks_master 테이블 형식으로 변환"""
        return {
            "symbol": symbol,
            "name": name,
            "market": market,
            "asset_type": asset_type,
            "is_active": True,
            "updated_at": datetime.now().isoformat()
        }

    @staticmethod
    def normalize_kis_supply(raw_row: Dict[str, Any], symbol: str, available_at: datetime) -> Dict[str, Any]:
        """
        KIS 투자자 수급 응답 1건을 normalized_stock_supply_daily 형식으로 변환
        """
        base_date = raw_row.get("base_date", available_at.strftime("%Y%m%d"))
        if len(base_date) == 8 and "-" not in base_date:
            base_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}"

        return {
            "symbol": symbol,
            "base_date": base_date,
            "foreign_net_buy": float(raw_row.get("foreign_net_buy", 0)),
            "institutional_net_buy": float(raw_row.get("institutional_net_buy", 0)),
            "individual_net_buy": float(raw_row.get("individual_net_buy", 0)),
            "foreign_holding_ratio": None,
            "short_volume": None,
            "short_balance": None,
            "lending_balance": None,
            "available_at": available_at.isoformat()
        }
