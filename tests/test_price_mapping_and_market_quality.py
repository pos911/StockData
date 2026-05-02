import json

import pytest

from src.collectors.kis.domestic import KISDomesticStockCollector
from src.collectors.kis.fundamentals import _parse_float_nullable
from src.loaders.supabase_loader import SupabaseLoader
from src.jobs.run_daily_stock_pipeline import _infer_asset_type, _standardize_market


class FakePriceCollector:
    def __init__(self, payload):
        self.payload = payload
        self.upserts = []

    async def _request(self, *_args, **_kwargs):
        return self.payload

    async def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True


@pytest.mark.asyncio
async def test_kis_ohlcv_maps_raw_fields_to_normalized_values():
    collector = FakePriceCollector(
        {
            "output2": [
                {
                    "stck_bsop_date": "20260501",
                    "stck_oprc": "70000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "69000",
                    "stck_clpr": "70500",
                    "acml_vol": "123456",
                    "acml_tr_pbmn": "8700000000",
                    "lstn_stcn": "5969782550",
                }
            ]
        }
    )

    records = await KISDomesticStockCollector.fetch_ohlcv(
        collector,
        "5930",
        start_date="20260501",
        end_date="20260501",
    )

    assert records[0]["symbol"] == "005930"
    assert records[0]["close_price"] == 70500
    assert records[0]["volume"] == 123456
    assert records[0]["trading_value"] == 8700000000

    raw_upsert = next(records for table, records in collector.upserts if table == "raw_stock_prices_daily")
    raw_payload = json.loads(raw_upsert[0]["raw_data"])
    assert raw_payload["response_row"]["stck_clpr"] == "70500"
    assert raw_payload["field_mapping"]["acml_tr_pbmn"] == "trading_value"


def test_loader_normalizes_numeric_symbol_to_six_digits():
    records = [{"symbol": "5930", "base_date": "2026-05-01", "close_price": 1, "volume": 1, "trading_value": 1}]
    normalized = SupabaseLoader._normalize_symbol_records("normalized_stock_prices_daily", records)
    assert normalized[0]["symbol"] == "005930"


def test_market_and_asset_type_standardization_keeps_etf_out_of_stock_rankings():
    assert _standardize_market("KOSPI", "KODEX 200") == "ETF"
    assert _infer_asset_type("KODEX 200", "KOSPI") == "ETF"
    assert _standardize_market("Q", "리노공업") == "KOSDAQ"
    assert _infer_asset_type("리노공업", "KOSDAQ") == "STOCK"


def test_failed_fundamental_ratio_parse_returns_null_not_zero():
    assert _parse_float_nullable("") is None
    assert _parse_float_nullable(None) is None
    assert _parse_float_nullable("not-a-number") is None
    assert _parse_float_nullable("0") == 0.0
