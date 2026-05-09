from typing import Any, Optional
import json
from datetime import date, datetime

from .base import KISBaseCollector
from .mapping import KIS_MAPPING
from src.utils.logger import get_logger
from src.utils.market_data_quality import is_valid_price_row
from src.utils.symbols import normalize_symbol_value

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
_SHORT_RATIO_CANDIDATES = (
    "short_sell_vol_rate",
    "short_ratio",
    "ssts_rate",
    "ssts_vol_rlim",
    "acml_ssts_cntg_qty_rlim",
    "acml_ssts_tr_pbmn_rlim",
)


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


def _parse_int_nullable(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _parse_float_nullable(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


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
        symbol = normalize_symbol_value(symbol)
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
        source_label: str = "KIS",
    ):
        symbol = normalize_symbol_value(symbol)
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
        normalized_records = []
        raw_records = []
        previous_market_cap = None
        now_iso = datetime.now().isoformat()
        for row in data["output2"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            close_price = _parse_int_nullable(row.get("stck_clpr"))
            listed_shares = _parse_int_nullable(row.get("lstn_stcn") or row.get("hts_avls"))
            market_cap = (
                close_price * listed_shares
                if close_price is not None and listed_shares is not None and listed_shares > 0
                else previous_market_cap
            )

            record = {
                "symbol": symbol,
                "base_date": formatted_date,
                "open_price": _parse_int_nullable(row.get("stck_oprc")),
                "high_price": _parse_int_nullable(row.get("stck_hgpr")),
                "low_price": _parse_int_nullable(row.get("stck_lwpr")),
                "close_price": close_price,
                "volume": _parse_int_nullable(row.get("acml_vol")),
                "trading_value": _parse_int_nullable(row.get("acml_tr_pbmn")),
                "market_cap": market_cap,
                "outstanding_shares": listed_shares,
                "source": source_label,
                "available_at": available_at or datetime.now().isoformat(),
            }
            records.append(record)
            if is_valid_price_row(record, market_is_open=True):
                normalized_records.append(record)
            raw_records.append(
                {
                    "source": source_label,
                    "symbol": symbol,
                    "base_date": record["base_date"],
                    "raw_data": json.dumps(
                        {
                            "response_row": row,
                            "field_mapping": {
                                "stck_clpr": "close_price",
                                "acml_vol": "volume",
                                "acml_tr_pbmn": "trading_value",
                                "stck_oprc": "open_price",
                                "stck_hgpr": "high_price",
                                "stck_lwpr": "low_price",
                            },
                            "source_base_date": record["base_date"],
                            "kis_tr_id": tr_id,
                            "source_label": source_label,
                        },
                        ensure_ascii=False,
                    ),
                    "collected_at": now_iso,
                    "available_at": available_at or now_iso,
                }
            )
            if market_cap is not None:
                previous_market_cap = market_cap

        await self.upsert_records("raw_stock_prices_daily", raw_records)
        await self.upsert_records("normalized_stock_prices_daily", normalized_records)
        return records

    async def fetch_investor_trend(self, symbol: str, available_at: Optional[str] = None):
        symbol = normalize_symbol_value(symbol)
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
                    # KIS *_ntby_qty fields are net buy quantities in shares.
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
        symbol = normalize_symbol_value(symbol)
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
        symbol = normalize_symbol_value(symbol)
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

                short_volume = _parse_int(_first_present(row, _SHORT_VOLUME_CANDIDATES, default=0))
                short_value = _parse_int(_first_present(row, _SHORT_VALUE_CANDIDATES, default=0))
                short_ratio = _parse_float_nullable(_first_present(row, _SHORT_RATIO_CANDIDATES, default=None))
                records.append(
                    {
                        "symbol": symbol,
                        "base_date": formatted_date,
                        "short_volume": short_volume,
                        "short_value": short_value,
                        "short_ratio": short_ratio,
                        "source": "KIS",
                        "available_at": available_at or now_iso,
                    }
                )

            if records:
                break

        if raw_records:
            await self.upsert_records("raw_stock_short_selling", raw_records)

        if not records:
            logger.warning(
                "Short selling collection returned no normalized rows: "
                f"symbol={symbol}, target_date={target_ymd}, api_rows={api_rows}, blocked_rows={blocked_rows}, attempts={attempts}"
            )
            return []

        logger.info(
            "Short selling collection summary: "
            f"symbol={symbol}, target_date={target_ymd}, api_rows={api_rows}, blocked_rows={blocked_rows}, "
            f"normalized_rows={len(records)}"
        )
        await self.upsert_records("normalized_stock_short_selling", records)
        return records

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

    async def fetch_fundamental_info(
        self,
        symbol: str,
        base_date: str,
        available_at: Optional[str] = None,
        source_label: str = "KIS",
    ) -> dict:
        symbol = normalize_symbol_value(symbol)
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
            "source": source_label,
            "available_at": available_at or datetime.now().isoformat(),
        }
        logger.debug(
            f"[fetch_fundamental_info] {symbol}: market_cap={record['market_cap']:,}, per={record['per']}, pbr={record['pbr']}"
        )
        return record

    async def fetch_kis_price_snapshot(
        self,
        symbol: str,
        base_date: str,
        available_at: Optional[str] = None,
        source_label: str = "KIS_DETAIL",
    ) -> dict:
        return await self.fetch_fundamental_info(
            symbol,
            base_date=base_date,
            available_at=available_at,
            source_label=source_label,
        )

    async def fetch_kis_daily_price(
        self,
        symbol: str,
        target_date: date | str,
        available_at: Optional[str] = None,
        source_label: str = "KIS_DETAIL",
    ) -> dict:
        target_ymd = target_date.strftime("%Y%m%d") if hasattr(target_date, "strftime") else str(target_date).replace("-", "")
        rows = await self.fetch_ohlcv(
            symbol,
            timeframe="D",
            start_date=target_ymd,
            end_date=target_ymd,
            available_at=available_at,
            source_label=source_label,
        )
        return rows[0] if rows else {}

    async def fetch_kis_stock_basic_info(self, symbol: str) -> dict:
        symbol = normalize_symbol_value(symbol)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", params=params)
        if not data or "output" not in data:
            return {}
        output = data["output"]
        return {
            "symbol": symbol,
            "name": output.get("hts_kor_isnm") or output.get("bstp_kor_isnm"),
            "market_cap": _parse_int_nullable(output.get("hts_avls")),
            "outstanding_shares": _parse_int_nullable(output.get("lstn_stcn")),
            "foreign_holding_ratio": _parse_float_nullable(output.get("hts_frgn_ehrt")),
            "per": _parse_float_nullable(output.get("per")),
            "pbr": _parse_float_nullable(output.get("pbr")),
        }

    async def fetch_kis_market_cap_info(self, symbol: str) -> dict:
        info = await self.fetch_kis_stock_basic_info(symbol)
        if not info:
            return {}
        market_cap = info.get("market_cap")
        return {
            "symbol": info.get("symbol"),
            "market_cap": market_cap * 100_000_000 if market_cap is not None else None,
            "outstanding_shares": info.get("outstanding_shares"),
        }

    async def fetch_kis_investment_ratios(
        self,
        symbol: str,
        base_date: str,
        available_at: Optional[str] = None,
        source_label: str = "KIS_DETAIL",
    ) -> dict:
        symbol = normalize_symbol_value(symbol)
        basic = await self.fetch_kis_stock_basic_info(symbol)
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_div_cls_code": "0",
        }
        data = await self._request(
            "GET",
            KIS_MAPPING["stability_ratio"]["path"],
            KIS_MAPPING["stability_ratio"]["tr_id"],
            params=params,
        )
        row = {}
        if data and "output" in data:
            output = data["output"]
            if isinstance(output, list) and output:
                row = output[0]
            elif isinstance(output, dict):
                row = output
        return {
            "symbol": symbol,
            "base_date": base_date,
            "per": basic.get("per"),
            "pbr": basic.get("pbr"),
            "roe": _parse_float_nullable(row.get("self_cptl_ntin_inrt")),
            "debt_ratio": _parse_float_nullable(row.get("lblt_rate")),
            "source": source_label,
            "available_at": available_at or datetime.now().isoformat(),
        }
