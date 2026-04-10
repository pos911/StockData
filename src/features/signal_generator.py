import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class SignalGenerator:
    """
    Feature Store의 Feature들을 활용하여 BUY/SELL/HOLD 시그널 생성.
    실제 계산된 수치 데이터를 기반으로 스코어링을 수행합니다.
    """
    def __init__(self):
        pass

    def generate_signal(self, features: Dict[str, float]) -> Dict[str, Any]:
        """
        주어진 Feature Dict에 대해 시그널 스코어를 계산합니다.
        
        가중치 설정:
        - Price: 0.3
        - Supply: 0.25
        - Macro: 0.2
        - Derivatives: 0.15
        - Event: 0.1
        """
        
        # 1. Price Score (이동평균 골든크로스 기반)
        ma5 = features.get("moving_avg_5")
        ma20 = features.get("moving_avg_20")
        
        price_score = 0.0
        if ma5 is not None and ma20 is not None:
            if ma5 > ma20:
                price_score = 0.8
            else:
                price_score = -0.5
        
        # 2. Supply Score (외국인 수급 Z-Score 기반)
        f_zscore = features.get("foreign_flow_zscore")
        
        supply_score = 0.0
        if f_zscore is not None:
            if f_zscore >= 1.0:
                supply_score = 0.9
            elif f_zscore <= -1.0:
                supply_score = -0.9
            else:
                supply_score = 0.0
        
        # 3. Macro Score (동적 반영)
        usdkrw_momentum = features.get("usdkrw_momentum", 0)
        risk_on_off = features.get("risk_on_off_score", 0)
        
        # 환율 모멘텀이 높으면 부정적(-0.5), 리스크온 상태면 긍정적(0.5)
        if usdkrw_momentum > 0.02:
            macro_score = -0.5
        elif risk_on_off > 0:
            macro_score = 0.5
        else:
            macro_score = 0.0
        
        # 4. Derivatives Score (basis_signal 연동)
        derivatives_score = features.get("basis_signal", 0.0)
        
        # 5. Event Score
        event_score = features.get("event_score", 0.0)

        # Total Weighted Score 계산
        total_score = (
            0.3 * price_score +
            0.25 * supply_score +
            0.2 * macro_score +
            0.15 * derivatives_score +
            0.1 * event_score
        )

        # 시그널 판정
        signal = "HOLD"
        if total_score > 0.5: # 스코어 기준 상향 조절 가능
            signal = "BUY"
        elif total_score < -0.2:
            signal = "SELL"

        return {
            "total_score": total_score,
            "signal": signal,
            "components": {
                "price": price_score,
                "supply": supply_score,
                "macro": macro_score,
                "derivatives": derivatives_score,
                "event": event_score
            }
        }
