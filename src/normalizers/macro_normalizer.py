from typing import Dict, Any, List
from datetime import datetime
from src.utils.time_utils import parse_date_string

class MacroNormalizer:
    """
    FRED, TradingEconomics 등의 매크로 원본 데이터를
    normalized_macro_series 스키마 구조로 정규화합니다.
    """
    
    @staticmethod
    def normalize_fred(series_id: str, raw_obs: List[Dict[str, Any]], available_at: datetime) -> List[Dict[str, Any]]:
        normalized = []
        for obs in raw_obs:
            val = obs.get("value", ".")
            if val == ".":
                continue # 누락값 제외
            
            normalized.append({
                "series_id": series_id,
                "base_date": obs.get("date"),
                "value": float(val),
                "available_at": available_at.isoformat()
            })
        return normalized

    @staticmethod
    def normalize_trading_economics(series_id: str, raw_data: List[Dict[str, Any]], available_at: datetime) -> List[Dict[str, Any]]:
        normalized = []
        for row in raw_data:
            # TE 데이터 구조에 맞춰 파싱 (가정)
            date_str = row.get("DateTime", "").split("T")[0]
            if not date_str:
                continue
                
            val = row.get("Value")
            if val is not None:
                normalized.append({
                    "series_id": series_id,
                    "base_date": date_str,
                    "value": float(val),
                    "available_at": available_at.isoformat()
                })
        return normalized
