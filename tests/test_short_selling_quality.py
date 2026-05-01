import pytest

from src.collectors.kis.domestic import KISDomesticStockCollector
from src.loaders.supabase_loader import SupabaseLoader


class FakeShortCollector:
    def __init__(self, payloads):
        self.payloads = payloads
        self.upserts = []

    async def _fetch_short_selling_payload(self, symbol, target_ymd, market_code):
        payload = self.payloads.get(market_code)
        if isinstance(payload, Exception):
            raise payload
        return payload, {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": target_ymd,
            "FID_INPUT_DATE_2": target_ymd,
        }, {"path": "/short", "tr_id": "TR"}

    async def upsert_records(self, table, records, **kwargs):
        self.upserts.append((table, records))
        return True

    def _fetch_short_selling_pykrx(self, *args, **kwargs):
        return []


@pytest.mark.asyncio
async def test_short_selling_uses_row_date():
    collector = FakeShortCollector({
        "J": {
            "output2": [
                {
                    "stck_bsop_date": "20260429",
                    "ssts_cntg_qty": "10",
                    "ssts_tr_pbmn": "1000",
                    "short_sell_vol_rate": "1.5",
                }
            ]
        }
    })
    records = await KISDomesticStockCollector.fetch_short_selling(collector, "005930", "20260429", market_code="J")
    assert records[0]["base_date"] == "2026-04-29"
    assert records[0]["short_volume"] == 10


@pytest.mark.asyncio
async def test_short_selling_uses_output1_date_when_row_date_missing():
    collector = FakeShortCollector({
        "J": {
            "output1": {"stck_bsop_date": "20260428"},
            "output2": [{"ssts_cntg_qty": "10", "ssts_tr_pbmn": "1000"}],
        }
    })
    records = await KISDomesticStockCollector.fetch_short_selling(collector, "005930", "20260429", market_code="J")
    assert records[0]["base_date"] == "2026-04-28"


@pytest.mark.asyncio
async def test_short_selling_falls_back_to_q_market():
    collector = FakeShortCollector({
        "J": {"output2": []},
        "Q": {"output2": [{"stck_bsop_date": "20260429", "ssts_cntg_qty": "5"}]},
    })
    records = await KISDomesticStockCollector.fetch_short_selling(collector, "058470", "20260429", market_code=None)
    assert records[0]["base_date"] == "2026-04-29"
    assert records[0]["short_volume"] == 5


@pytest.mark.asyncio
async def test_short_selling_continues_when_first_market_code_errors():
    collector = FakeShortCollector({
        "Q": RuntimeError("ERROR INVALID FID_COND_MRKT_DIV_CODE"),
        "J": {"output2": [{"stck_bsop_date": "20260429", "ssts_cntg_qty": "7"}]},
    })
    records = await KISDomesticStockCollector.fetch_short_selling(collector, "058470", "20260429", market_code="KOSDAQ")
    assert records[0]["base_date"] == "2026-04-29"
    assert records[0]["short_volume"] == 7


def test_loader_blocks_missing_conflict_keys():
    loader = SupabaseLoader.__new__(SupabaseLoader)
    records = [
        {"symbol": "005930", "base_date": "", "short_volume": 1},
        {"symbol": "005930", "base_date": "2026-04-29", "short_volume": 2},
    ]
    valid = SupabaseLoader._validate_conflict_keys(
        loader,
        "normalized_stock_short_selling",
        records,
        ["symbol", "base_date"],
        False,
    )
    assert valid == [{"symbol": "005930", "base_date": "2026-04-29", "short_volume": 2}]
