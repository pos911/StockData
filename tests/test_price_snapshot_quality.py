import pytest

from src.jobs.run_daily_stock_pipeline import _repair_missing_snapshot_fields
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.dynamic_universe_loader import DynamicUniverseLoader


class FakeExecuteResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.filters = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        return FakeExecuteResult(self.data)


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return FakeQuery(self.tables.get(name, []))


class FakeLoader:
    def __init__(self, price_rows):
        self.upserts = []
        self.updates = []
        self.client = FakeClient(
            {
                "stocks_master": [{"symbol": "005930"}],
                "normalized_stock_prices_daily": price_rows,
                "normalized_stock_supply_daily": [],
            }
        )

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True

    def update_record(self, table, match_fields, update_fields, **_kwargs):
        self.updates.append((table, match_fields, update_fields))
        return True


class FakeCollector:
    async def fetch_fundamental_info(self, symbol, base_date, available_at):
        return {
            "symbol": symbol,
            "base_date": base_date,
            "market_cap": 1000,
            "listed_shares": 10,
            "foreign_holding_ratio": 50.0,
            "per": 10.0,
            "pbr": 1.0,
            "source": "KIS",
            "available_at": available_at,
        }


@pytest.mark.asyncio
async def test_repair_does_not_create_price_row_when_price_row_missing():
    loader = FakeLoader(price_rows=[])
    await _repair_missing_snapshot_fields(loader, FakeCollector(), __import__("datetime").date(2026, 5, 1), "2026-05-01T18:00:00+09:00")

    assert not any(table == "normalized_stock_prices_daily" for table, _records in loader.upserts)
    assert any(table == "normalized_stock_snapshots_daily" for table, _records in loader.upserts)
    assert loader.updates == []


@pytest.mark.asyncio
async def test_repair_updates_snapshot_fields_for_existing_valid_price_row():
    loader = FakeLoader(
        price_rows=[
            {
                "symbol": "005930",
                "base_date": "2026-05-01",
                "open_price": 1,
                "high_price": 2,
                "low_price": 1,
                "close_price": 2,
                "volume": 100,
                "trading_value": 200,
                "market_cap": None,
                "outstanding_shares": None,
            }
        ]
    )
    await _repair_missing_snapshot_fields(loader, FakeCollector(), __import__("datetime").date(2026, 5, 1), "2026-05-01T18:00:00+09:00")

    assert not any(table == "normalized_stock_prices_daily" for table, _records in loader.upserts)
    assert loader.updates
    table, match_fields, update_fields = loader.updates[0]
    assert table == "normalized_stock_prices_daily"
    assert match_fields == {"symbol": "005930", "base_date": "2026-05-01"}
    assert update_fields["market_cap"] == 1000
    assert update_fields["outstanding_shares"] == 10


def test_loader_blocks_snapshot_only_price_payload():
    loader = SupabaseLoader.__new__(SupabaseLoader)
    records = [
        {
            "symbol": "005930",
            "base_date": "2026-05-01",
            "market_cap": 1000,
            "outstanding_shares": 10,
        }
    ]
    valid = SupabaseLoader._validate_table_records(loader, "normalized_stock_prices_daily", records, False)
    assert valid == []


def test_cleanup_sql_targets_snapshot_only_rows():
    with open("sql/cleanup_snapshot_only_price_rows.sql", "r", encoding="utf-8") as handle:
        sql = handle.read()
    assert "DELETE FROM public.normalized_stock_prices_daily" in sql
    for column in ("close_price", "volume", "trading_value", "open_price", "high_price", "low_price"):
        assert f"{column} IS NULL" in sql


def test_ranking_rows_are_persisted_to_raw_and_normalized_tables():
    class RankingLoader:
        def __init__(self):
            self.upserts = []

        def upsert_records(self, table, records, **_kwargs):
            self.upserts.append((table, records))
            return True

    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.loader = RankingLoader()

    rows = [
        {
            "mksc_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "acml_vol": "100",
            "acml_tr_pbmn": "100000",
            "prdy_ctrt": "1.2",
        }
    ]
    universe = DynamicUniverseLoader._persist_rankings(loader, rows, "KOSPI", "volume", 30)

    assert universe == [{"code": "005930", "name": "삼성전자", "market": "KOSPI"}]
    tables = {table for table, _records in loader.loader.upserts}
    assert "raw_market_rankings" in tables
    assert "normalized_market_rankings_daily" in tables
