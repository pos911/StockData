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


def diagnose_krx_web_market(target_date: date, market_id: str) -> dict[str, Any]:
    endpoint = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    params = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "trdDd": target_date.strftime("%Y%m%d"),
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
        "mktId": market_id,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    started = time.perf_counter()
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=20)
        elapsed = round(time.perf_counter() - started, 3)
        body_sample = _safe_text(response.text or "")
        content_type = response.headers.get("content-type", "")
        parseable = False
        rows = []
        try:
            payload = response.json()
            parseable = True
            for key in ("OutBlock_1", "output", "output1", "block1"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    rows = candidate
                    break
        except Exception:
            payload = None

        collector = KRXCollector(auth_key="")
        normalized = [
            collector.normalize_krx_stock_price_row({**row, "MKT_ID": market_id}, target_date)
            for row in rows
        ]
        normalized = [row for row in normalized if row]
        valid_rows = [row for row in normalized if is_valid_price_row(row, market_is_open=True)]
        status = "SUCCESS_KRX_WEB_STOCK_PRICE_SOURCE" if len(valid_rows) >= 1000 else "FAIL"
        return {
            "endpoint": endpoint,
            "params": params,
            "status_code": response.status_code,
            "content_type": content_type,
            "body_sample": body_sample,
            "json_parseable": parseable,
            "row_count": len(rows),
            "columns": sorted(rows[0].keys()) if rows else [],
            "sample_row": _safe_sample_row(rows[0]) if rows else None,
            "normalized_valid_rows": len(valid_rows),
            "elapsed_seconds": elapsed,
            "status": status,
        }
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "endpoint": endpoint,
            "params": params,
            "status_code": None,
            "content_type": None,
            "body_sample": "",
            "json_parseable": False,
            "row_count": 0,
            "columns": [],
            "sample_row": None,
            "normalized_valid_rows": 0,
            "elapsed_seconds": elapsed,
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
        }


def diagnose_open_api_candidates(auth_key: str, target_date: date) -> list[dict[str, Any]]:
    endpoint_candidates = [
        ("https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr", {"basDd": target_date.strftime("%Y%m%d")}),
        ("https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_clpr", {"basDd": target_date.strftime("%Y%m%d")}),
        ("https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd", {"basDd": target_date.strftime("%Y%m%d")}),
    ]
    results = []
    for endpoint, params in endpoint_candidates:
        started = time.perf_counter()
        try:
            response = requests.get(endpoint, params=params, headers={"AUTH_KEY": auth_key}, timeout=20)
            elapsed = round(time.perf_counter() - started, 3)
            body_sample = _safe_text(response.text or "")
            content_type = response.headers.get("content-type", "")
            parseable = False
            rows = []
            try:
                payload = response.json()
                parseable = True
                rows = payload.get("OutBlock_1") or []
                if not isinstance(rows, list):
                    rows = []
            except Exception:
                pass
            results.append(
                {
                    "endpoint": endpoint,
                    "params": params,
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "body_sample": body_sample,
                    "json_parseable": parseable,
                    "row_count": len(rows),
                    "columns": sorted(rows[0].keys()) if rows else [],
                    "sample_row": _safe_sample_row(rows[0]) if rows else None,
                    "elapsed_seconds": elapsed,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "endpoint": endpoint,
                    "params": params,
                    "status_code": None,
                    "content_type": None,
                    "body_sample": "",
                    "json_parseable": False,
                    "row_count": 0,
                    "columns": [],
                    "sample_row": None,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return results


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
    result: dict[str, Any] = {
        "stock_listing": None,
        "symbols": [],
    }
    try:
        df = fdr.StockListing("KRX")
        result["stock_listing"] = {
            "shape": tuple(df.shape),
            "columns": list(df.columns),
            "head": df.head(3).to_dict(orient="records"),
        }
    except Exception as exc:
        result["stock_listing"] = {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    for symbol in REPRESENTATIVE_SYMBOLS:
        try:
            df = fdr.DataReader(symbol, target_day[:4] + "-" + target_day[4:6] + "-" + target_day[6:8], target_day[:4] + "-" + target_day[4:6] + "-" + target_day[6:8])
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
                {
                    "symbol": symbol,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="20260506")
    args = parser.parse_args()

    target_date = parse_date_string(args.date)
    config = load_config()
    auth_key = config.get("krx", {}).get("auth_key", "")

    print("=== KRX_WEB MDCSTAT01501 Diagnosis ===")
    print(f"krx_auth_key_present={bool(auth_key)}")
    stk_target = diagnose_krx_web_market(target_date, "STK")
    ksq_target = diagnose_krx_web_market(target_date, "KSQ")
    stk_prev = diagnose_krx_web_market(date(2026, 5, 4), "STK")
    ksq_prev = diagnose_krx_web_market(date(2026, 5, 4), "KSQ")
    for payload in (stk_target, ksq_target, stk_prev, ksq_prev):
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    combined_valid_rows = stk_target["normalized_valid_rows"] + ksq_target["normalized_valid_rows"]
    print(
        json.dumps(
            {
                "date": target_date.strftime("%Y%m%d"),
                "combined_valid_rows": combined_valid_rows,
                "status": "SUCCESS_KRX_WEB_STOCK_PRICE_SOURCE" if combined_valid_rows >= 2000 else "FAIL",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n=== KRX Open API Fallback Diagnosis ===")
    print(json.dumps(diagnose_open_api_candidates(auth_key, target_date), ensure_ascii=False, indent=2))

    print("\n=== pykrx Diagnosis (target date) ===")
    print(json.dumps(diagnose_pykrx_day(target_date.strftime("%Y%m%d")), ensure_ascii=False, indent=2))

    print("\n=== pykrx Diagnosis (20260504) ===")
    print(json.dumps(diagnose_pykrx_day("20260504"), ensure_ascii=False, indent=2))

    print("\n=== FinanceDataReader Diagnosis ===")
    print(json.dumps(diagnose_fdr(target_date.strftime("%Y%m%d")), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
