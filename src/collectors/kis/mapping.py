"""
KIS API 엔드포인트 및 TR_ID 매핑 정의 파일.
공식 샘플(koreainvestment/open-trading-api) 기준 정합성을 보장합니다.
"""

# 카테고리 프리픽스
CAT_QUOTATIONS = "/uapi/domestic-stock/v1/quotations"
CAT_FINANCE = "/uapi/domestic-stock/v1/finance"

KIS_MAPPING = {
    # 1. 시세 및 상세 (Quotations)
    "ohlcv": {
        "path": f"{CAT_QUOTATIONS}/inquire-daily-itemchartprice",
        "tr_id": "FHKST03010100",
        "description": "국내주식 기간별 시세(일/주/월)"
    },
    "investor_trend": {
        "path": f"{CAT_QUOTATIONS}/inquire-investor",
        "tr_id": "FHKST01010900",
        "description": "국내주식 투자자별 매매동향"
    },
    "short_selling": {
        "path": f"{CAT_QUOTATIONS}/daily-short-sale",
        "tr_id": "FHPST04830000",
        "description": "국내주식 일별 공매도 통계"
    },
    
    # 2. 재무 및 지표 (Finance)
    "stability_ratio": {
        "path": f"{CAT_FINANCE}/stability-ratio",
        "tr_id": "FHKST66430600",
        "description": "국내주식 재무비율 - 안정성지표"
    },
    "growth_ratio": {
        "path": f"{CAT_FINANCE}/growth-ratio",
        "tr_id": "FHKST66430800", # 성장성비율 공식 TR ID
        "description": "국내주식 재무비율 - 성장성지표"
    },
    "financial_ratio": {
        "path": f"{CAT_FINANCE}/financial-ratio",
        "tr_id": "FHKST66430300", # 공식 "재무비율" TR ID
        "description": "국내주식 - 기타 재무비율"
    },
    "profitability_ratio": {
        "path": f"{CAT_FINANCE}/profit-ratio", # 수익성비율 공식 Slug
        "tr_id": "FHKST66430400",
        "description": "국내주식 재무비율 - 수익성지표"
    }
}
