from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import FinanceDataReader as fdr
import requests
from pykrx import stock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.collectors.krx_collector import KRXCollector
from src.utils.config_loader import load_config
from src.utils.market_data_quality import is_valid_price_row
from src.utils.time_utils import parse_date_string


REPRESENTATIVE_SYMBOLS = ["005930", "000660", "035720", "058470"]
KOSPI_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"


def _safe_text(text: str, limit: int = 500) -> str:
    return (text or "")[:limit].replace("\n", " ").replace("\r", " ")


def _safe_sample_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    safe = {}
    for key, value in row.items():
        if isinstance(value, str) and len(value) > 200:
            safe[key] = value[:200] + "...<truncated>"
        else:
            safe[key] = value
    return safe


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("OutBlock_1", "output", "output1", "block1"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return candidate
    return []


def diagnose_stk_bydd_trd_request(auth_key: str, target_date: date, method: str, request_style: str) -> dict[str, Any]:
    payload = {"basDd": target_date.strftime("%Y%m%d")}
    headers = {
        "AUTH_KEY": auth_key,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if request_style == "json":
        headers["Content-Type"] = "application/json"
    elif request_style == "form":
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    started = time.perf_counter()
    try:
        if method == "GET":
            response = requests.get(KOSPI_ENDPOINT, params=payload, headers=headers, timeout=20)
        elif request_style == "json":
            response = requests.post(KOSPI_ENDPOINT, json=payload, headers=headers, timeout=20)
        else:
            response = requests.post(KOSPI_ENDPOINT, data=payload, headers=headers, timeout=20)
        elapsed = round(time.perf_counter() - started, 3)
        content_type = response.headers.get("content-type", "")
        body_sample = _safe_text(response.text or "")

        parseable = False
        rows: list[dict[str, Any]] = []
        payload_json = None
        try:
            payload_json = response.json()
            parseable = True
            rows = _extract_rows(payload_json)
        except Exception:
            pass

        collector = KRXCollector(auth_key=auth_key)
        normalized = [collector.normalize_krx_stock_price_row(row, target_date) for row in rows]
        normalized = [row for row in normalized if row]
        valid_rows = [row for row in normalized if is_valid_price_row(row, market_is_open=True)]
        return {
            "method": method,
            "request_style": request_style,
            "url": KOSPI_ENDPOINT if method == "POST" else f"{KOSPI_ENDPOINT}?basDd={payload['basDd']}",
            "status_code": response.status_code,
            "content_type": content_type,
            "body_sample": body_sample,
            "json_parseable": parseable,
            "row_count": len(rows),
            "columns": sorted(rows[0].keys()) if rows else [],
            "sample_row": _safe_sample_row(rows[0]) if rows else None,
            "normalized_valid_rows": len(valid_rows),
            "elapsed_seconds": elapsed,
            "error_message": None,
        }
    except Exception as exc:
        return {
            "method": method,
            "request_style": request_style,
            "url": KOSPI_ENDPOINT,
            "status_code": None,
            "content_type": None,
            "body_sample": "",
            "json_parseable": False,
            "row_count": 0,
            "columns": [],
            "sample_row": None,
            "normalized_valid_rows": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error_message": f"{type(exc).__name__}: {exc}",
        }


def diagnose_pykrx_day(target_day: str) -> list[dict[str, Any]]:
    results = []
    for market_name in ("KOSPI", "KOSDAQ"):
        payload: dict[str, Any] = {
            "date": target_day,
            "market": market_name,
            "exception_type": None,
            "exception_message": None,
            "shape": None,
            "columns": None,
            "head": None,
        }
        try:
            df = stock.get_market_ohlcv_by_ticker(target_day, market=market_name)
            payload["shape"] = tuple(df.shape)
            payload["columns"] = list(df.columns)
            payload["head"] = df.head(3).reset_index().to_dict(orient="records")
        except Exception as exc:
            payload["exception_type"] = type(exc).__name__
            payload["exception_message"] = str(exc)
        results.append(payload)
    return results


def diagnose_fdr(target_day: str) -> dict[str, Any]:
    result: dict[str, Any] = {"stock_listing": None, "symbols": []}
    try:
        df = fdr.StockListing("KRX")
        result["stock_listing"] = {
            "shape": tuple(df.shape),
            "columns": list(df.columns),
            "head": df.head(3).to_dict(orient="records"),
        }
    except Exception as exc:
        result["stock_listing"] = {"error_type": type(exc).__name__, "error_message": str(exc)}

    start = f"{target_day[:4]}-{target_day[4:6]}-{target_day[6:8]}"
    for symbol in REPRESENTATIVE_SYMBOLS:
        try:
            df = fdr.DataReader(symbol, start, start)
            result["symbols"].append(
                {
                    "symbol": symbol,
                    "shape": tuple(df.shape),
                    "columns": list(df.columns),
                    "head": df.head(3).reset_index().astype(str).to_dict(orient="records"),
                }
            )
        except Exception as exc:
            result["symbols"].append(
                {"symbol": symbol, "error_type": type(exc).__name__, "error_message": str(exc)}
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="20260506")
    args = parser.parse_args()

    target_date = parse_date_string(args.date)
    config = load_config()
    auth_key = config.get("krx", {}).get("auth_key", "")

    print("=== KRX stk_bydd_trd Request Format Diagnosis ===")
    print(f"krx_auth_key_present={bool(auth_key)}")
    attempts = [
        ("GET", "query"),
        ("POST", "json"),
        ("POST", "form"),
    ]
    results = [
        diagnose_stk_bydd_trd_request(auth_key, target_date, method, request_style)
        for method, request_style in attempts
    ]
    for result in results:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    best_valid_rows = max(result.get("normalized_valid_rows", 0) for result in results)
    success_method = next(
        (
            f"{result['method']} {result['request_style']}"
            for result in results
            if result.get("normalized_valid_rows", 0) == best_valid_rows and best_valid_rows > 0
        ),
        None,
    )
    print(
        json.dumps(
            {
                "diagnosis": "stk_bydd_trd",
                "best_method": success_method,
                "best_valid_rows": best_valid_rows,
                "status": "SUCCESS" if best_valid_rows > 0 else "FAIL",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n=== KOSDAQ Endpoint Candidate Status ===")
    print(
        json.dumps(
            {
                "status": "NOT_IMPLEMENTED",
                "note": "KOSDAQ daily trading endpoint must be confirmed from official KRX API documentation before production use.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n=== pykrx Diagnosis (target date) ===")
    print(json.dumps(diagnose_pykrx_day(target_date.strftime("%Y%m%d")), ensure_ascii=False, indent=2))

    print("\n=== pykrx Diagnosis (20260504) ===")
    print(json.dumps(diagnose_pykrx_day("20260504"), ensure_ascii=False, indent=2))

    print("\n=== FinanceDataReader Diagnosis ===")
    print(json.dumps(diagnose_fdr(target_date.strftime("%Y%m%d")), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
