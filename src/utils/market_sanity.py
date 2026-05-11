import math
from typing import Any, Dict, Optional

def is_hard_invalid_index_value(value: Any) -> bool:
    """Check if the index value is mathematically impossible or missing."""
    if value is None:
        return True
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val) or val <= 0:
            return True
        return False
    except (ValueError, TypeError):
        return True

def compute_change_rate(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Compute percentage change safely."""
    if current is None or previous is None or previous == 0:
        return None
    return ((current / previous) - 1.0) * 100.0

def classify_index_quality(
    value: Optional[float],
    source: str,
    source_change_rate: Optional[float] = None,
    previous_value: Optional[float] = None,
    secondary_value: Optional[float] = None,
    anomaly_threshold_pct: float = 20.0,
    mismatch_threshold_pct: float = 10.0
) -> str:
    """
    Classify the quality of a macro index value without hard upper bounds.
    
    Returns:
        One of: 'OK', 'INVALID', 'ANOMALY', 'SOURCE_MISMATCH'
    """
    if is_hard_invalid_index_value(value):
        return "INVALID"
        
    val = float(value)
    
    # 1. Check change rate anomaly (if source provided it)
    if source_change_rate is not None:
        if abs(source_change_rate) > anomaly_threshold_pct:
            return "ANOMALY"
            
    # 2. Check change rate anomaly (computed from previous)
    if source_change_rate is None and previous_value is not None and previous_value > 0:
        calc_change = compute_change_rate(val, previous_value)
        if calc_change is not None and abs(calc_change) > anomaly_threshold_pct:
            return "ANOMALY"
            
    # 3. Check source mismatch
    if secondary_value is not None and secondary_value > 0:
        mismatch_pct = abs((val / secondary_value) - 1.0) * 100.0
        if mismatch_pct > mismatch_threshold_pct:
            return "SOURCE_MISMATCH"
            
    return "OK"
