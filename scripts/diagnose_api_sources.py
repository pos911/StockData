import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from typing import Any

sys.path.append(os.getcwd())

from src.collectors.ecos_client import EcosClient
from src.collectors.kis.auth import KISAuthManager
from src.collectors.kis.base import KISBaseCollector
from src.collectors.kis.domestic import KISDomesticStockCollector, _DATE_CANDIDATES, _normalize_date_value
from src.collectors.kis.mapping import KIS_MAPPING
from src.utils.config_loader import load_config
from src.utils.time_utils import get_current_kst


def _safe_json(value: Any, limit: int = 2000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit] + ("...<truncated>" if len(text) > limit else "")


def _rows_and_output1(payload: dict | None) -> tuple[list, dict]:
    if not payload:
        return [], {}
    output1 = payload.get("output1") or {}
    if isinstance(output1, list):
        output1 = output1[0] if output1 else {}
    rows = payload.get("output2") or payload.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows or [], output1 if isinstance(output1, dict) else {}


def _diagnose_payload(payload: dict | None, params: dict, market: str, symbol: str, date: str, source: str):
    rows, output1 = _rows_and_output1(payload)
    first = rows[0] if rows else {}
    date_candidates = {}
    for key in _DATE_CANDIDATES:
        if key in first:
            date_candidates[f"row.{key}"] = first.get(key)
        if key in output1:
            date_candidates[f"output1.{key}"] = output1.get(key)

    number_candidates = {
        key: first.get(key)
        for key in (
            "ssts_cntg_qty",
            "ssts_tr_pbmn",
            "short_sell_vol_rate",
            "ssts_vol",
            "ssts_amt",
            "short_volume",
            "short_value",
        )
        if key in first
    }

    print(
        _safe_json(
            {
                "source": source,
                "symbol": symbol,
                "date": date,
                "market": market,
                "params": params,
                "top_keys": sorted(list(payload.keys())) if payload else [],
                "output1_keys": sorted(list(output1.keys())) if output1 else [],
                "row_count": len(rows),
                "first_row_keys": sorted(list(first.keys())) if first else [],
                "first_row_sample": first,
                "date_candidates": date_candidates,
                "normalized_date_candidates": {
                    key: _normalize_date_value(value) for key, value in date_candidates.items()
                },
                "number_candidates": number_candidates,
                "diagnosis": _diagnosis(rows, first, date_candidates),
            },
            limit=6000,
        )
    )


def _diagnosis(rows: list, first: dict, date_candidates: dict) -> list[str]:
    notes = []
    if not rows:
        notes.extend(
            [
                "API value missing for requested date/market.",
                "Possible market-code mismatch.",
                "Possible publication delay or non-business day.",
                "Possible API entitlement or unsupported endpoint.",
            ]
        )
    elif not date_candidates:
        notes.append("Rows exist but no known date field was found; output1/request-date fallback is required.")
    if first and not any(key in first for key in ("ssts_cntg_qty", "ssts_tr_pbmn", "short_sell_vol_rate")):
        notes.append("Rows exist but expected KIS short-selling numeric keys are missing.")
    return notes


async def diagnose_kis(args):
    config = load_config()
    auth = KISAuthManager(config)
    await auth.initialize()
    collector = KISDomesticStockCollector(config, auth, asyncio.Semaphore(1))
    mapping_key = {
        "kis_short_selling": "short_selling",
        "kis_investor": "investor_trend",
        "kis_ohlcv": "ohlcv",
        "kis_volume_rank": None,
    }[args.source]
    mapping = KIS_MAPPING[mapping_key] if mapping_key else {
        "path": "/uapi/domestic-stock/v1/quotations/volume-rank",
        "tr_id": "FHPST01710000",
    }
    markets = ["J", "Q"] if args.market == "auto" else [args.market]
    try:
        for market in markets:
            if args.source == "kis_short_selling":
                params = {
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_INPUT_ISCD": args.symbol,
                    "FID_INPUT_DATE_1": args.date,
                    "FID_INPUT_DATE_2": args.date,
                }
            elif args.source == "kis_volume_rank":
                params = {
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_BLNG_CLS_CODE": "0",
                    "FID_TRGT_CLS_CODE": "0",
                    "FID_TRGT_EXLS_CLS_CODE": "0",
                    "FID_INPUT_PRICE_1": "",
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_cnt": "",
                }
            elif args.source == "kis_investor":
                params = {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": args.symbol}
            else:
                params = {
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_INPUT_ISCD": args.symbol,
                    "FID_INPUT_DATE_1": args.date,
                    "FID_INPUT_DATE_2": args.date,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "1",
                }
            payload = await collector._request("GET", mapping["path"], mapping["tr_id"], params=params)
            _diagnose_payload(payload, params, market, args.symbol or "", args.date or "", args.source)
    finally:
        await KISBaseCollector.close_session()


def diagnose_ecos(args):
    config = load_config()
    api_key = config.get("ecos", {}).get("api_key")
    client = EcosClient(api_key=api_key)
    with open("config/ecos_series.json", "r", encoding="utf-8") as handle:
        definitions = json.load(handle)
    definition = next((row for row in definitions if row["series_id"] == args.series), None)
    if not definition:
        raise SystemExit(f"Unknown ECOS series_id: {args.series}")
    end = args.date or get_current_kst().date().strftime("%Y%m%d")
    start = (get_current_kst().date() - timedelta(days=14)).strftime("%Y%m%d")
    rows = client.fetch_statistic(
        definition["stat_code"],
        definition["item_code"],
        definition["cycle"],
        start,
        end,
        series_id=definition["series_id"],
    )
    print(_safe_json({"series": args.series, "start": start, "end": end, "row_count": len(rows), "sample": rows[:3]}, 6000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--date", help="YYYYMMDD")
    parser.add_argument("--source", required=True, choices=["kis_short_selling", "kis_investor", "kis_ohlcv", "kis_volume_rank", "ecos"])
    parser.add_argument("--market", default="auto", choices=["J", "Q", "T", "auto"])
    parser.add_argument("--series", help="ECOS series_id")
    args = parser.parse_args()
    if args.source == "ecos":
        diagnose_ecos(args)
    else:
        if args.source == "kis_volume_rank":
            asyncio.run(diagnose_kis(args))
            return
        if not args.symbol or not args.date:
            raise SystemExit("--symbol and --date are required for KIS diagnostics")
        asyncio.run(diagnose_kis(args))


if __name__ == "__main__":
    main()
