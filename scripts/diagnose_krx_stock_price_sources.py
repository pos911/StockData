from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import FinanceDataReader as fdr
import requests
from pykrx import stock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


ENDPOINT_CANDIDATES = [
    {
        "endpoint": "https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr",
        "params": {"basDd": "20260506"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_clpr",
        "params": {"basDd": "20260506"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506"},
    },
    {
        "endpoint": "https://data.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506", "mktId": "STK"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506", "mktId": "KSQ"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"trdDd": "20260506"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506", "share": "1", "money": "1"},
    },
    {
        "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "params": {"basDd": "20260506", "csvxls_isNo": "false"},
    },
]

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


def diagnose_endpoint(auth_key: str, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    headers = {"AUTH_KEY": auth_key} if auth_key else {}
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=20)
        body_text = response.text or ""
        content_type = response.headers.get("content-type", "")
        parsed_json = None
        outblock_rows = []
        parseable = False
        try:
            parsed_json = response.json()
            parseable = True
            if isinstance(parsed_json, dict):
                outblock_rows = parsed_json.get("OutBlock_1") or []
                if not isinstance(outblock_rows, list):
                    outblock_rows = []
        except Exception:
            parsed_json = None
        columns = sorted(outblock_rows[0].keys()) if outblock_rows else []
        return {
            "endpoint": endpoint,
            "params": params,
            "status_code": response.status_code,
            "content_type": content_type,
            "body_sample": _safe_text(body_text),
            "json_parseable": parseable,
            "row_count": len(outblock_rows),
            "columns": columns,
            "sample_row": _safe_sample_row(outblock_rows[0]) if outblock_rows else None,
        }
    except Exception as exc:
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
            "error": f"{type(exc).__name__}: {exc}",
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


def diagnose_fdr() -> dict[str, Any]:
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
            df = fdr.DataReader(symbol, "2026-05-06", "2026-05-06")
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
    config = load_config()
    auth_key = config.get("krx", {}).get("auth_key", "")
    print("=== KRX Endpoint Diagnosis ===")
    print(f"krx_auth_key_present={bool(auth_key)}")
    for candidate in ENDPOINT_CANDIDATES:
        result = diagnose_endpoint(auth_key, candidate["endpoint"], candidate["params"])
        print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n=== pykrx Diagnosis (20260506) ===")
    print(json.dumps(diagnose_pykrx_day("20260506"), ensure_ascii=False, indent=2))

    print("\n=== pykrx Diagnosis (20260504) ===")
    print(json.dumps(diagnose_pykrx_day("20260504"), ensure_ascii=False, indent=2))

    print("\n=== FinanceDataReader Diagnosis ===")
    print(json.dumps(diagnose_fdr(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
