from typing import Optional
import json
from datetime import datetime

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

    async def fetch_short_selling(self, symbol: str, available_at: Optional[str] = None):
        mapping = KIS_MAPPING["short_selling"]
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"),
        }
        data = await self._request("GET", mapping["path"], mapping["tr_id"], params=params)
        if not data or "output2" not in data:
            return []

        records = []
        for row in data["output2"]:
            base_date = row.get("stck_bsop_date")
            formatted_date = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:8]}" if base_date else ""
            records.append(
                {
                    "symbol": symbol,
                    "base_date": formatted_date,
                    "short_volume": _parse_int(row.get("ssts_cntg_qty", 0)),
                    "short_value": _parse_int(row.get("ssts_tr_pbmn", 0)),
                    "short_ratio": _parse_float(row.get("short_sell_vol_rate", 0)),
                    "source": "KIS",
                    "available_at": available_at or datetime.now().isoformat(),
                }
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
