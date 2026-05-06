from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.collectors.kis.mapping import KIS_MAPPING


def main() -> None:
    rows = [
        {
            "purpose": "daily_price",
            "path": KIS_MAPPING["ohlcv"]["path"],
            "tr_id": KIS_MAPPING["ohlcv"]["tr_id"],
            "request_params": ["FID_COND_MRKT_DIV_CODE", "FID_INPUT_ISCD", "FID_INPUT_DATE_1", "FID_INPUT_DATE_2", "FID_PERIOD_DIV_CODE", "FID_ORG_ADJ_PRC"],
            "response_fields": ["stck_bsop_date", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol", "acml_tr_pbmn", "lstn_stcn"],
        },
        {
            "purpose": "basic_info/current_price/market_cap/outstanding_shares/per_pbr/foreign_holding_ratio",
            "path": "/uapi/domestic-stock/v1/quotations/inquire-price",
            "tr_id": "FHKST01010100",
            "request_params": ["FID_COND_MRKT_DIV_CODE", "FID_INPUT_ISCD"],
            "response_fields": ["hts_avls", "lstn_stcn", "per", "pbr", "hts_frgn_ehrt", "w52_hgpr", "w52_lwpr"],
        },
        {
            "purpose": "investment_ratios/debt_ratio/roe_candidate",
            "path": KIS_MAPPING["stability_ratio"]["path"],
            "tr_id": KIS_MAPPING["stability_ratio"]["tr_id"],
            "request_params": ["fid_cond_mrkt_div_code", "fid_input_iscd", "fid_div_cls_code"],
            "response_fields": ["lblt_rate", "self_cptl_ntin_inrt"],
        },
        {
            "purpose": "volume_rank",
            "path": "/uapi/domestic-stock/v1/quotations/volume-rank",
            "tr_id": "FHPST01710000",
            "request_params": ["FID_COND_MRKT_DIV_CODE", "FID_COND_SCR_DIV_CODE", "FID_INPUT_ISCD"],
            "response_fields": ["mksc_shrn_iscd", "hts_kor_isnm", "acml_vol", "acml_tr_pbmn", "prdy_ctrt"],
        },
        {
            "purpose": "market_cap_only_dedicated_endpoint",
            "path": "NOT_FOUND",
            "tr_id": "NOT_FOUND",
            "request_params": [],
            "response_fields": [],
        },
        {
            "purpose": "foreign_holding_ratio_dedicated_endpoint",
            "path": "NOT_FOUND",
            "tr_id": "NOT_FOUND",
            "request_params": [],
            "response_fields": [],
        },
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
