import asyncio

import pytest

from src.collectors.krx_collector import KRXCollector
from src.jobs.run_daily_master_pipeline import _master_record
from src.jobs.run_daily_ranking_pipeline import _filter_kis_volume_rows, _persist_rankings
from src.jobs.run_daily_stock_pipeline import (
    _enforce_detail_universe_guardrail,
    _should_skip_full_universe_price_ingestion,
)
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.market_data_quality import get_latest_valid_price_date


def test_normalize_krx_etf_row():
    collector = KRXCollector(auth_key="")
    row = {
        "BAS_DD": "20260502",
        "ISU_CD": "069500",
        "ISU_NM": "KODEX 200",
        "TDD_CLSPRC": "35,100",
        "TDD_OPNPRC": "35,000",
        "TDD_HGPRC": "35,200",
        "TDD_LWPRC": "34,950",
        "ACC_TRDVOL": "1,234,567",
        "ACC_TRDVAL": "43,210,000,000",
        "MKTCAP": "5,000,000,000,000",
        "LIST_SHRS": "142,000,000",
    }
    normalized = collector.normalize_krx_etp_row(row, "ETF", "ETF", __import__("datetime").date(2026, 5, 2))
    assert normalized["symbol"] == "069500"
    assert normalized["market"] == "ETF"
    assert normalized["asset_type"] == "ETF"
    assert normalized["close_price"] == 35100.0


def test_normalize_krx_etn_row():
    collector = KRXCollector(auth_key="")
    row = {
        "BAS_DD": "20260502",
        "ISU_CD": "550001",
        "ISU_NM": "Sample ETN",
        "TDD_CLSPRC": "10,010",
        "TDD_OPNPRC": "10,000",
        "TDD_HGPRC": "10,100",
        "TDD_LWPRC": "9,990",
        "ACC_TRDVOL": "12,345",
        "ACC_TRDVAL": "123,450,000",
        "MKTCAP": "999,999,999",
        "LIST_SHRS": "100,000",
    }
    normalized = collector.normalize_krx_etp_row(row, "ETN", "ETN", __import__("datetime").date(2026, 5, 2))
    assert normalized["symbol"] == "550001"
    assert normalized["market"] == "ETN"
    assert normalized["asset_type"] == "ETN"


def test_kis_volume_rank_filters_to_approved_market_and_normalizes_symbol():
    master_map = {
        "005930": {"market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
        "069500": {"market": "ETF", "asset_type": "ETF", "name": "KODEX 200"},
        "058470": {"market": "KOSDAQ", "asset_type": "STOCK", "name": "Leeno"},
    }
    rows = [
        {"mksc_shrn_iscd": "Q005930", "hts_kor_isnm": "Samsung", "acml_vol": "100", "acml_tr_pbmn": "1000"},
        {"mksc_shrn_iscd": "069500", "hts_kor_isnm": "KODEX 200", "acml_vol": "200", "acml_tr_pbmn": "2000"},
        {"mksc_shrn_iscd": "058470", "hts_kor_isnm": "Leeno", "acml_vol": "300", "acml_tr_pbmn": "3000"},
    ]
    kospi = _filter_kis_volume_rows(rows, master_map, "KOSPI")
    kosdaq = _filter_kis_volume_rows(rows, master_map, "KOSDAQ")
    assert [row["symbol"] for row in kospi] == ["005930"]
    assert [row["symbol"] for row in kosdaq] == ["058470"]


class _FakeDeleteQuery:
    def __init__(self):
        self.filters = []

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        return self


class _FakeTable:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name

    def delete(self):
        query = _FakeDeleteQuery()
        self.parent.deletes.append((self.name, query))
        return query


class _FakeClient:
    def __init__(self):
        self.deletes = []

    def table(self, name):
        return _FakeTable(self, name)


class _FakeLoader:
    def __init__(self):
        self.client = _FakeClient()
        self.upserts = []

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True


def test_persist_rankings_deletes_same_source_combo_and_reassigns_rank():
    loader = _FakeLoader()
    rows = [
        {"symbol": "005930", "name": "Samsung", "volume": 100, "trading_value": 1000, "market_cap": None, "change_rate": None, "metric_value": 100, "raw_data": {}},
        {"symbol": "000660", "name": "SK hynix", "volume": 90, "trading_value": 900, "market_cap": None, "change_rate": None, "metric_value": 90, "raw_data": {}},
    ]
    count = _persist_rankings(loader, __import__("datetime").date(2026, 5, 3), "KOSPI", "volume", "KIS", rows)
    assert count == 2
    normalized = next(records for table, records in loader.upserts if table == "normalized_market_rankings_daily")
    assert [row["rank"] for row in normalized] == [1, 2]


def test_master_record_has_no_rank_field():
    record = _master_record("005930", "Samsung", "KOSPI", "STOCK", {}, set())
    assert "rank" not in record


def test_new_master_symbol_defaults_to_inactive():
    record = _master_record("005930", "Samsung", "KOSPI", "STOCK", {}, set())
    assert record["is_active"] is False


def test_existing_active_symbol_is_preserved():
    record = _master_record("005930", "Samsung", "KOSPI", "STOCK", {"005930": True}, set())
    assert record["is_active"] is True


def test_static_enabled_symbol_is_protected():
    record = _master_record("005930", "Samsung", "KOSPI", "STOCK", {"005930": False}, {"005930"})
    assert record["is_active"] is True


@pytest.mark.asyncio
async def test_dynamic_universe_loader_uses_ranking_table_without_kis_calls():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.static_universe_path = "config/__missing_stock_universe__.json"

    class NoKISCollector:
        async def fetch_volume_rank(self, *args, **kwargs):
            raise AssertionError("KIS ranking API should not be called")

    loader.collector = NoKISCollector()

    class TableResult:
        def __init__(self, data):
            self.data = data

    class Query:
        def __init__(self, data):
            self.data = data

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return TableResult(self.data)

    class Client:
        def table(self, name):
            if name == "stocks_master":
                return Query([
                    {"symbol": "005930", "name": "Samsung", "market": "KOSPI", "asset_type": "STOCK", "is_active": True},
                    {"symbol": "000660", "name": "SK hynix", "market": "KOSPI", "asset_type": "STOCK", "is_active": True},
                ])
            if name == "normalized_market_rankings_daily":
                return Query([
                    {"base_date": "2026-05-03"},
                    {"symbol": "005930", "name": "Samsung", "market": "KOSPI", "rank_type": "volume"},
                ])
            if name == "static_stock_universe":
                return Query([])
            return Query([])

    class FakeSupabaseLoader:
        def __init__(self):
            self.client = Client()

    loader.loader = FakeSupabaseLoader()
    universe = await loader.get_combined_universe(auto_backfill=False)
    assert any(row["symbol"] == "005930" for row in universe)
    assert all(row["symbol"] != "000660" for row in universe)
    assert all("active_master" not in row.get("source_category", "") for row in universe)


@pytest.mark.asyncio
async def test_dynamic_universe_loader_prefers_static_and_ranking_only():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.static_universe_path = "config/__missing_stock_universe__.json"
    loader.collector = None

    class TableResult:
        def __init__(self, data):
            self.data = data

    class Query:
        def __init__(self, data):
            self.data = data

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return TableResult(self.data)

    class Client:
        def table(self, name):
            if name == "stocks_master":
                return Query(
                    [
                        {"symbol": "005930", "name": "Samsung", "market": "KOSPI", "asset_type": "STOCK", "is_active": False},
                        {"symbol": "000660", "name": "SK hynix", "market": "KOSPI", "asset_type": "STOCK", "is_active": True},
                        {"symbol": "058470", "name": "Leeno", "market": "KOSDAQ", "asset_type": "STOCK", "is_active": False},
                    ]
                )
            if name == "normalized_market_rankings_daily":
                return Query(
                    [
                        {"base_date": "2026-05-03"},
                        {"symbol": "005930", "name": "Samsung", "market": "KOSPI", "rank_type": "volume"},
                    ]
                )
            if name == "static_stock_universe":
                return Query([{"symbol": "058470", "name": "Leeno", "market": "KOSDAQ"}])
            return Query([])

    class FakeSupabaseLoader:
        def __init__(self):
            self.client = Client()

    loader.loader = FakeSupabaseLoader()
    universe = await loader.get_combined_universe(auto_backfill=False)
    symbols = {row["symbol"] for row in universe}
    assert symbols == {"005930", "058470"}
    by_symbol = {row["symbol"]: row for row in universe}
    assert by_symbol["005930"]["source_category"] == "ranking"
    assert by_symbol["058470"]["source_category"] == "static"


def test_detail_universe_guardrail_truncates_when_too_large():
    universe = [{"symbol": f"{i:06d}", "source_category": "ranking"} for i in range(600)]
    trimmed = _enforce_detail_universe_guardrail(universe, None, active_master_count=2400)
    assert len(trimmed) == 500


def test_detail_universe_guardrail_keeps_limit_override():
    universe = [{"symbol": f"{i:06d}", "source_category": "ranking"} for i in range(600)]
    trimmed = _enforce_detail_universe_guardrail(universe, 50, active_master_count=2400)
    assert len(trimmed) == 600


def test_full_universe_price_ingestion_guardrail_skip():
    assert _should_skip_full_universe_price_ingestion(2000) is True
    assert _should_skip_full_universe_price_ingestion(1999) is False


class _PriceDateResult:
    def __init__(self, data):
        self.data = data


class _PriceDateQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
        self._order_desc = False
        self._limit = None

    def select(self, *_args, **_kwargs):
        return self

    def gte(self, key, value):
        self.filters.setdefault("gte", {})[key] = value
        return self

    def lte(self, key, value):
        self.filters.setdefault("lte", {})[key] = value
        return self

    def eq(self, key, value):
        self.filters.setdefault("eq", {})[key] = value
        return self

    def order(self, _key, desc=False):
        self._order_desc = desc
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = list(self.rows)
        for key, value in self.filters.get("eq", {}).items():
            rows = [row for row in rows if row.get(key) == value]
        for key, value in self.filters.get("gte", {}).items():
            rows = [row for row in rows if row.get(key) >= value]
        for key, value in self.filters.get("lte", {}).items():
            rows = [row for row in rows if row.get(key) <= value]
        if rows and "base_date" in rows[0]:
            rows = sorted(rows, key=lambda row: row.get("base_date"), reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _PriceDateResult(rows)


class _PriceDateClient:
    def __init__(self, table_map):
        self.table_map = table_map

    def table(self, name):
        return _PriceDateQuery(self.table_map.get(name, []))


class _PriceDateLoader:
    def __init__(self, table_map):
        self.client = _PriceDateClient(table_map)


def test_latest_valid_price_date_prefers_latest_valid_not_simple_max():
    loader = _PriceDateLoader(
        {
            "stocks_master": [
                {"symbol": "005930", "market": "KOSPI", "asset_type": "STOCK"},
                {"symbol": "000660", "market": "KOSPI", "asset_type": "STOCK"},
            ],
            "normalized_stock_prices_daily": [
                {"symbol": "005930", "base_date": "2026-05-04", "close_price": None, "volume": None, "trading_value": None},
                {"symbol": "000660", "base_date": "2026-05-04", "close_price": None, "volume": None, "trading_value": None},
                {"symbol": "005930", "base_date": "2026-05-03", "close_price": 1, "volume": 10, "trading_value": 100},
                {"symbol": "000660", "base_date": "2026-05-03", "close_price": 2, "volume": 20, "trading_value": 200},
            ],
        }
    )
    result = get_latest_valid_price_date(loader, __import__("datetime").date(2026, 5, 4), lookback_days=5, min_valid_rows=2)
    assert result["selected_price_base_date"] == "2026-05-03"
