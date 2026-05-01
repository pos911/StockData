from typing import Any, Optional
import json
from datetime import date, datetime

from .base import KISBaseCollector
from .mapping import KIS_MAPPING
from src.utils.logger import get_logger

logger = get_logger(__name__)


_INVESTOR_REQUIRED_KEYS = {
    "stck_bsop_date",
    "prsn_ntby_qty",
    "orgn_ntby_qty",
    "frgn_ntby_qty",
}

_DATE_CANDIDATES = ("stck_bsop_date", "bsop_date", "trd_dd", "date", "bas_dt", "stck_dt")
_SHORT_VOLUME_CANDIDATES = ("ssts_cntg_qty", "ssts_vol", "short_volume", "short_sell_qty")
_SHORT_VALUE_CANDIDATES = ("ssts_tr_pbmn", "ssts_amt", "short_value", "short_sell_amt")
_SHORT_RATIO_CANDIDATES = ("short_sell_vol_rate", "short_ratio", "ssts_rate")


def _parse_int(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


def _parse_float(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _normalize_date_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
        except ValueError:
            return ""
    return ""


def _first_present(row: dict, keys: tuple[str, ...], default=0):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _kis_market_codes(market_code: Optional[str]) -> list[str]:
    value = (market_code or "").upper()
    if value in {"J", "KOSPI", "STOCK"}:
        return ["J", "Q"]
    if value in {"Q", "KOSDAQ"}:
        return ["Q", "J"]
    return ["J", "Q"]


def _extract_output_rows(data: Optional[dict]) -> tuple[list[dict], dict]:
    if not data:
        return [], {}
    output1 = data.get("output1") or {}
    if isinstance(output1, list):
        output1 = output1[0] if output1 else {}
    rows = data.get("output2") or data.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows or [], output1 if isinstance(output1, dict) else {}


def _resolve_row_date(row: dict, output1: dict, target_date: str) -> str:
    for key in _DATE_CANDIDATES:
        parsed = _normalize_date_value(row.get(key))
        if parsed:
            return parsed
    for key in _DATE_CANDIDATES:
        parsed = _normalize_date_value(output1.get(key))
        if parsed:
            return parsed
    return _normalize_date_value(target_date)


class KISDomesticStockCollector(KISBaseCollector):
    @staticmethod
    def _all_zero_investor_flow(records) -> bool:
        if not records:
            return False
        return all(
            record["individual_net_buy"] == 0
            and record["institutional_net_buy"] == 0
            and record["foreign_net_buy"] == 0
            and record["pension_net_buy"] == 0
            and record["corporate_net_buy"] == 0
            for record in records
        )

    @staticmethod
    def _investor_keys_present(rows) -> bool:
        if not rows:
            return False
        row_keys = set(rows[0].keys())
        return _INVESTOR_REQUIRED_KEYS.issubset(row_keys)

    async def _fetch_investor_payload(self, symbol: str):
        mapping = KIS_MAPPING["investor_trend"]
        attempts = []

        for market_div_code in ("J", "Q"):
            params = {
                "FID_COND_MRKT_DIV_CODE": market_div_code,
                "FID_INPUT_ISCD": symbol,
            }
            data = await self._request("GET", mapping["path"], mapping["tr_id"], params=params)
            rows = data.get("output", []) if data else []
            attempts.append((market_div_code, rows))

            if rows and self._investor_keys_present(rows):
                if market_div_code == "Q":
                    logger.info(f"Investor trend for {symbol} succeeded with KOSDAQ market code fallback.")
                return market_div_code, rows

        for market_div_code, rows in attempts:
            if rows:
                sample_keys = sorted(rows[0].keys())
                logger.error(
                    f"KIS investor trend response for {symbol} with market code {market_div_code} "
                    f"is missing expected keys. sample_keys={sample_keys}"
                )
        return None, []

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "D",
        start_date: str = "",
        end_date: str = "",
        available_at: Optional[str] = None,
    ):
        mapping = KIS_MAPPING["ohlcv"]
        tr_id = mapping["tr_id"] if timeframe == "D" else "FHKST03010200"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": timeframe,
            "FID_ORG_ADJ_PRC": "1",
        }

        data = await self._request("GET", mapping["path"], tr_id, params=params)
        if not data or "output2" not in data:
            return []

        records = []
        previous_market_cap = None
        for row in data["output2"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            close_price = _parse_int(row.get("stck_clpr", 0))
            listed_shares = _parse_int(row.get("lstn_stcn") or row.get("hts_avls") or 0)
            market_cap = close_price * listed_shares if listed_shares > 0 else previous_market_cap

            record = {
                "symbol": symbol,
                "base_date": formatted_date,
                "open_price": _parse_int(row.get("stck_oprc", 0)),
                "high_price": _parse_int(row.get("stck_hgpr", 0)),
                "low_price": _parse_int(row.get("stck_lwpr", 0)),
                "close_price": close_price,
                "volume": _parse_int(row.get("acml_vol", 0)),
                "trading_value": _parse_int(row.get("acml_tr_pbmn", 0)),
                "market_cap": market_cap,
                "source": "KIS",
                "available_at": available_at or datetime.now().isoformat(),
            }
            records.append(record)
            if market_cap is not None:
                previous_market_cap = market_cap

        raw_records = [
            {
                "source": "KIS",
                "symbol": symbol,
                "base_date": record["base_date"],
                "raw_data": json.dumps(record),
                "collected_at": datetime.now().isoformat(),
                "available_at": datetime.now().isoformat(),
            }
            for record in records
        ]

        await self.upsert_records("raw_stock_prices_daily", raw_records)
        await self.upsert_records("normalized_stock_prices_daily", records)
        return records

    async def fetch_investor_trend(self, symbol: str, available_at: Optional[str] = None):
        market_div_code, rows = await self._fetch_investor_payload(symbol)
        if not rows:
            return []

        records = []
        raw_records = []
        now_iso = datetime.now().isoformat()

        for row in rows:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""

            individual = _parse_int(row.get("prsn_ntby_qty", 0))
            institutional = _parse_int(row.get("orgn_ntby_qty", 0))
            foreign = _parse_int(row.get("frgn_ntby_qty", 0))
            pension = _parse_int(row.get("pnsn_ntby_qty", 0))
            corporate = _parse_int(row.get("etc_corp_ntby_qty", 0))

            if individual == 0 and institutional == 0 and foreign == 0 and pension == 0 and corporate == 0:
                logger.warning(f"Zero investor flow detected for {symbol}")

            records.append(
                {
                    "symbol": symbol,
                    "base_date": formatted_date,
                    "individual_net_buy": individual,
                    "institutional_net_buy": institutional,
                    "foreign_net_buy": foreign,
                    "pension_net_buy": pension,
                    "corporate_net_buy": corporate,
                    "source": "KIS",
                    "available_at": available_at or now_iso,
                }
            )
            raw_records.append(
                {
                "source": "KIS",
                    "symbol": symbol,
                    "base_date": formatted_date,
                    "raw_data": json.dumps(
                        {
                            "market_div_code": market_div_code,
                            "response_row": row,
                        },
                        ensure_ascii=False,
                    ),
                    "collected_at": now_iso,
                    "available_at": available_at or now_iso,
                }
            )

        if self._all_zero_investor_flow(records):
            sample_keys = sorted(rows[0].keys()) if rows else []
            logger.error(
                f"KIS investor trend returned only zero values for {symbol}. "
                f"market_div_code={market_div_code}, sample_keys={sample_keys}"
            )
            await self.upsert_records("raw_stock_supply_daily", raw_records)
            return []

        await self.upsert_records("raw_stock_supply_daily", raw_records)
        await self.upsert_records("normalized_stock_supply_daily", records)
        return records

    async def _fetch_short_selling_payload(self, symbol: str, target_ymd: str, market_code: str):
        mapping = KIS_MAPPING["short_selling"]
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": target_ymd,
            "FID_INPUT_DATE_2": target_ymd,
        }
        data = await self._request("GET", mapping["path"], mapping["tr_id"], params=params)
        return data, params, mapping

    async def fetch_short_selling(
        self,
        symbol: str,
        target_date: date | str,
        market_code: Optional[str] = None,
        available_at: Optional[str] = None,
    ):
        target_ymd = target_date.strftime("%Y%m%d") if hasattr(target_date, "strftime") else str(target_date).replace("-", "")
        now_iso = datetime.now().isoformat()
        attempts = []
        records = []
        raw_records = []
        blocked_rows = 0
        api_rows = 0

        for resolved_market in _kis_market_codes(market_code):
            try:
                data, params, mapping = await self._fetch_short_selling_payload(symbol, target_ymd, resolved_market)
            except Exception as exc:
                mapping = KIS_MAPPING["short_selling"]
                params = {
                    "FID_COND_MRKT_DIV_CODE": resolved_market,
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": target_ymd,
                    "FID_INPUT_DATE_2": target_ymd,
                }
                attempts.append(
                    {
                        "source": "KIS",
                        "market_code": resolved_market,
                        "endpoint": mapping["path"],
                        "tr_id": mapping["tr_id"],
                        "params": params,
                        "error": str(exc),
                    }
                )
                logger.warning(
                    f"KIS short selling request failed; trying next market code if available: "
                    f"symbol={symbol}, target_date={target_ymd}, market_code={resolved_market}, error={exc}"
                )
                continue
            rows, output1 = _extract_output_rows(data)
            api_rows += len(rows)
            attempts.append(
                {
                    "source": "KIS",
                    "market_code": resolved_market,
                    "endpoint": mapping["path"],
                    "tr_id": mapping["tr_id"],
                    "params": params,
                    "top_keys": sorted(list(data.keys())) if data else [],
                    "output1_keys": sorted(list(output1.keys())) if output1 else [],
                    "row_count": len(rows),
                    "sample_row": rows[0] if rows else None,
                }
            )

            if not rows:
                logger.warning(
                    f"KIS short selling empty response: symbol={symbol}, target_date={target_ymd}, "
                    f"market_code={resolved_market}, endpoint={mapping['path']}, tr_id={mapping['tr_id']}"
                )
                continue

            for row in rows:
                formatted_date = _resolve_row_date(row, output1, target_ymd)
                raw_records.append(
                    {
                        "source": "KIS",
                        "symbol": symbol,
                        "base_date": formatted_date or _normalize_date_value(target_ymd),
                        "raw_data": json.dumps(
                            {
                                "market_code": resolved_market,
                                "endpoint": mapping["path"],
                                "tr_id": mapping["tr_id"],
                                "params": params,
                                "output1": output1,
                                "response_row": row,
                            },
                            ensure_ascii=False,
                        ),
                        "collected_at": now_iso,
                        "available_at": available_at or now_iso,
                    }
                )
                if not symbol or not formatted_date:
                    blocked_rows += 1
                    logger.warning(
                        f"Blocked short selling row without required keys: symbol={symbol}, "
                        f"base_date={formatted_date!r}, target_date={target_ymd}, market_code={resolved_market}, "
                        f"row_keys={sorted(row.keys())}, output1_keys={sorted(output1.keys())}"
                    )
                    continue
                records.append(
                    {
                        "symbol": symbol,
                        "base_date": formatted_date,
                        "short_volume": _parse_int(_first_present(row, _SHORT_VOLUME_CANDIDATES)),
                        "short_value": _parse_int(_first_present(row, _SHORT_VALUE_CANDIDATES)),
                        "short_ratio": _parse_float(_first_present(row, _SHORT_RATIO_CANDIDATES)),
                        "source": "KIS",
                        "available_at": available_at or now_iso,
                    }
                )
            if records:
                break

        if raw_records:
            await self.upsert_records("raw_stock_short_selling", raw_records)

        if not records:
            fallback = self._fetch_short_selling_pykrx(symbol, target_ymd, available_at=available_at or now_iso)
            if fallback:
                records.extend(fallback)

        if records:
            ok = await self.upsert_records("normalized_stock_short_selling", records)
            if ok is False:
                logger.error(f"Short selling normalized upsert failed for {symbol} on {target_ymd}.")
        else:
            logger.warning(
                f"No short selling records collected after KIS/fallback attempts: symbol={symbol}, "
                f"target_date={target_ymd}, attempts={json.dumps(attempts[:2], ensure_ascii=False, default=str)[:1200]}"
            )

        logger.info(
            f"Short selling collection summary: symbol={symbol}, target_date={target_ymd}, "
            f"api_rows={api_rows}, success_rows={len(records)}, blocked_rows={blocked_rows}"
        )
        return records

    def _fetch_short_selling_pykrx(self, symbol: str, target_ymd: str, available_at: Optional[str] = None):
        try:
            from pykrx import stock

            functions = [
                "get_shorting_status_by_date",
                "get_shorting_volume_by_date",
                "get_shorting_value_by_date",
            ]
            available_functions = [name for name in functions if hasattr(stock, name)]
            if not available_functions:
                logger.warning("pykrx short selling functions are unavailable.")
                return []

            for function_name in available_functions:
                func = getattr(stock, function_name)
                try:
                    df = func(target_ymd, target_ymd, symbol)
                except TypeError:
                    continue
                if df is None or df.empty:
                    continue

                row = df.iloc[-1].to_dict()
                base_date = _normalize_date_value(str(df.index[-1])) or _normalize_date_value(target_ymd)
                logger.info(f"PYKRX short selling fallback succeeded: symbol={symbol}, function={function_name}")
                return [
                    {
                        "symbol": symbol,
                        "base_date": base_date,
                        "short_volume": _parse_int(_first_present(row, ("공매도", "수량", "short_volume", "거래량"))),
                        "short_value": _parse_int(_first_present(row, ("금액", "거래대금", "short_value"))),
                        "short_ratio": _parse_float(_first_present(row, ("비중", "비중(%)", "short_ratio"))),
                        "source": "PYKRX",
                        "available_at": available_at or datetime.now().isoformat(),
                    }
                ]
        except Exception as exc:
            logger.warning(f"PYKRX short selling fallback failed for {symbol}: {exc}")
        return []

    async def fetch_volume_rank(self, market_code: str = "J", target_code: str = "0000"):
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": target_code,
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "0",
            "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_cnt": "",
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", params=params)
        return data.get("output", []) if data else []

    async def fetch_market_cap_rank(self, market_code: str = "J"):
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_COND_SCR_DIV_CODE": "20174",
            "FID_DIV_CLS_CODE": "0",
            "FID_INPUT_ISCD": "0000",
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/market-cap", "FHPST01740000", params=params)
        return data.get("output", []) if data else []

    async def fetch_fundamental_info(self, symbol: str, base_date: str, available_at: Optional[str] = None) -> dict:
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", params=params)
        if not data or "output" not in data:
            return {}

        output = data["output"]
        record = {
            "symbol": symbol,
            "base_date": base_date,
            "market_cap": _parse_int(output.get("hts_avls", 0)) * 100_000_000,
            "per": _parse_float(output.get("per", 0)),
            "pbr": _parse_float(output.get("pbr", 0)),
            "w52_high": _parse_int(output.get("w52_hgpr", 0)),
            "w52_low": _parse_int(output.get("w52_lwpr", 0)),
            "listed_shares": _parse_int(output.get("lstn_stcn", 0)),
            "foreign_holding_ratio": _parse_float(output.get("hts_frgn_ehrt", 0)),
            "source": "KIS",
            "available_at": available_at or datetime.now().isoformat(),
        }
        logger.debug(
            f"[fetch_fundamental_info] {symbol}: market_cap={record['market_cap']:,}, per={record['per']}, pbr={record['pbr']}"
        )
        return record
