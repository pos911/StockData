from __future__ import annotations

import asyncio
import json

from src.jobs import run_daily_macro_pipeline as macro_module
from src.jobs import run_daily_ranking_pipeline as ranking_module


def test_kis_j_rows_are_classified_by_master_market():
    master_map = {
        "005930": {"market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
        "058470": {"market": "KOSDAQ", "asset_type": "STOCK", "name": "Leeno"},
        "069500": {"market": "ETF", "asset_type": "ETF", "name": "KODEX 200"},
        "550001": {"market": "ETN", "asset_type": "ETN", "name": "Sample ETN"},
    }
    rows = [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "Samsung", "acml_vol": "100", "acml_tr_pbmn": "1000"},
        {"mksc_shrn_iscd": "058470", "hts_kor_isnm": "Leeno", "acml_vol": "200", "acml_tr_pbmn": "2000"},
        {"mksc_shrn_iscd": "069500", "hts_kor_isnm": "KODEX 200", "acml_vol": "300", "acml_tr_pbmn": "3000"},
        {"mksc_shrn_iscd": "550001", "hts_kor_isnm": "Sample ETN", "acml_vol": "400", "acml_tr_pbmn": "4000"},
    ]
    buckets = ranking_module._classify_kis_volume_rows(rows, master_map)
    assert [row["symbol"] for row in buckets["KOSPI"]] == ["005930"]
    assert [row["symbol"] for row in buckets["KOSDAQ"]] == ["058470"]
    assert [row["symbol"] for row in buckets["ETF"]] == ["069500"]
    assert [row["symbol"] for row in buckets["ETN"]] == ["550001"]


class _Result:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = {}
        self._order_desc = False
        self._limit = None

    def select(self, *_args, **_kwargs):
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
        return _Result(rows)

    def eq(self, key, value):
        self.filters.setdefault("eq", {})[key] = value
        return self

    def gte(self, key, value):
        self.filters.setdefault("gte", {})[key] = value
        return self

    def lte(self, key, value):
        self.filters.setdefault("lte", {})[key] = value
        return self

    def order(self, _key, desc=False):
        self._order_desc = desc
        return self

    def limit(self, value):
        self._limit = value
        return self

    def delete(self):
        return self


class _Loader:
    def __init__(self, table_map):
        self.table_map = table_map
        self.client = self
        self.upserts = []
        self.logs = []

    def table(self, name):
        return _Query(self.table_map.get(name, []))

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True

    def insert_log(self, *args):
        self.logs.append(args)


def test_kosdaq_volume_fallback_is_generated_from_valid_price():
    stock_rows = [{"symbol": f"{i:06d}", "market": "KOSPI", "asset_type": "STOCK", "name": f"Name{i}"} for i in range(100000, 100099)]
    price_rows = [{"symbol": f"{i:06d}", "base_date": "2026-05-03", "close_price": 1, "volume": 10, "trading_value": 100, "market_cap": 10} for i in range(100000, 100099)]
    loader = _Loader(
        {
            "stocks_master": stock_rows + [
                {"symbol": "005930", "market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
                {"symbol": "058470", "market": "KOSDAQ", "asset_type": "STOCK", "name": "Leeno"},
                {"symbol": "069500", "market": "ETF", "asset_type": "ETF", "name": "KODEX 200"},
            ],
            "normalized_stock_prices_daily": price_rows + [
                {"symbol": "005930", "base_date": "2026-05-03", "close_price": 1, "volume": 100, "trading_value": 1000, "market_cap": 10},
                {"symbol": "058470", "base_date": "2026-05-03", "close_price": 2, "volume": 200, "trading_value": 3000, "market_cap": 20},
                {"symbol": "069500", "base_date": "2026-05-03", "close_price": 3, "volume": 150, "trading_value": 2500, "market_cap": 30},
            ],
        }
    )
    master_map = ranking_module._load_master_map(loader)
    source, rows = ranking_module._select_volume_rankings(
        loader=loader,
        target_date=__import__("datetime").date(2026, 5, 4),
        market="KOSDAQ",
        kis_rows=[],
        master_map=master_map,
    )
    assert source == "VALID_PRICE_FALLBACK"
    assert [row["symbol"] for row in rows] == ["058470"]
    assert rows[0]["raw_data"]["price_base_date"] == "2026-05-03"
    assert rows[0]["raw_data"]["fallback_reason"] == "kis_volume_sparse"
    assert rows[0]["raw_data"]["original_kis_count"] == 0


def test_trading_value_and_market_cap_rankings_use_valid_price_date():
    stock_rows = [{"symbol": f"{i:06d}", "market": "KOSPI", "asset_type": "STOCK", "name": f"Name{i}"} for i in range(200000, 200099)]
    price_rows = [{"symbol": f"{i:06d}", "base_date": "2026-05-03", "close_price": 1, "volume": 10, "trading_value": 100, "market_cap": 10} for i in range(200000, 200099)]
    loader = _Loader(
        {
            "stocks_master": stock_rows + [
                {"symbol": "005930", "market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
            ],
            "normalized_stock_prices_daily": price_rows + [
                {"symbol": "005930", "base_date": "2026-05-04", "close_price": None, "volume": None, "trading_value": None, "market_cap": None},
                {"symbol": "005930", "base_date": "2026-05-03", "close_price": 1, "volume": 100, "trading_value": 1000, "market_cap": 9999},
            ],
        }
    )
    master_map = ranking_module._load_master_map(loader)
    source, rows, price_base_date = ranking_module._build_price_based_rankings(
        loader, __import__("datetime").date(2026, 5, 4), master_map, "KOSPI", "trading_value", 30
    )
    assert source == "VALID_PRICE_FALLBACK"
    assert price_base_date == "2026-05-03"
    assert rows[0]["raw_data"]["price_base_date"] == "2026-05-03"

    _source2, rows2, price_base_date2 = ranking_module._build_price_based_rankings(
        loader, __import__("datetime").date(2026, 5, 4), master_map, "KOSPI", "market_cap", 30
    )
    assert price_base_date2 == "2026-05-03"
    assert rows2[0]["metric_value"] == 9999


def test_run_daily_ranking_pipeline_uses_j_only(monkeypatch):
    class FakeAuth:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    class FakeCollector:
        def __init__(self, *_args, **_kwargs):
            self.calls = []

        async def fetch_volume_rank(self, market_code="J", target_code="0000"):
            self.calls.append((market_code, target_code))
            assert market_code == "J"
            return []

    class FakeLoader(_Loader):
        pass

    fake_loader = FakeLoader(
        {
            "stocks_master": [
                {"symbol": "005930", "market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
            ],
            "normalized_stock_prices_daily": [
                {"symbol": "005930", "base_date": "2026-05-04", "close_price": 1, "volume": 100, "trading_value": 1000, "market_cap": 5000},
            ],
        }
    )

    monkeypatch.setattr(ranking_module, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}, "kis": {}})
    monkeypatch.setattr(ranking_module, "SupabaseLoader", lambda **_kwargs: fake_loader)
    monkeypatch.setattr(ranking_module, "KISAuthManager", FakeAuth)
    monkeypatch.setattr(ranking_module, "KISDomesticStockCollector", FakeCollector)
    async def _close_session():
        return None

    monkeypatch.setattr(ranking_module.KISBaseCollector, "close_session", staticmethod(_close_session))
    asyncio.run(ranking_module.run_pipeline(__import__("datetime").date(2026, 5, 4), dry_run=True))


def test_macro_pipeline_global_record_includes_us3y(monkeypatch):
    class FakeLoader:
        def __init__(self, *_args, **_kwargs):
            self.client = self
            self.upserts = []
            self.logs = []

        def table(self, name):
            if name == "normalized_macro_series":
                return _Query(
                    [
                        {"series_id": "DGS3", "base_date": "2026-05-04", "value": 3.11},
                        {"series_id": "DGS10", "base_date": "2026-05-04", "value": 4.21},
                        {"series_id": "KR_GOVT_10Y", "base_date": "2026-05-04", "value": 3.2},
                        {"series_id": "USDKRW", "base_date": "2026-05-04", "value": 1400.0},
                    ]
                )
            return _Query([])

        def upsert_records(self, table, records, **_kwargs):
            self.upserts.append((table, records))
            return True

        def fetch_all(self, *_args, **_kwargs):
            return []

        def insert_log(self, *args):
            self.logs.append(args)

    class FakeFRED:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch_series(self, series_id, **_kwargs):
            if series_id == "DGS3":
                return {"observations": [{"date": "2026-05-04", "value": "3.11"}]}
            if series_id == "DGS10":
                return {"observations": [{"date": "2026-05-04", "value": "4.21"}]}
            if series_id == "BAMLH0A0HYM2":
                return {"observations": [{"date": "2026-05-04", "value": "3.50"}]}
            return {"observations": []}

    class FakeGlobalIndex:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch_daily_indices(self, _target_date):
            return {
                "base_date": "2026-05-04",
                "usdkrw": 1400.0,
                "dxy": 100.0,
                "us10y": 4.21,
                "kospi": 2800.0,
                "kospi_change_rate": 0.1,
                "kosdaq": 900.0,
                "kosdaq_change_rate": 0.2,
                "wti": 70.0,
                "brent": 72.0,
                "nasdaq": 18000.0,
                "nasdaq_change_rate": 0.3,
                "sp500": 5000.0,
                "sp500_change_rate": 0.4,
                "sox": 4000.0,
                "vix": 15.0,
                "gold": 2300.0,
                "copper": 4.5,
                "bdry": 10.0,
                "kospi_individual_net_buy": 1.0,
                "kospi_foreign_net_buy": 2.0,
                "kospi_institutional_net_buy": 3.0,
                "kosdaq_individual_net_buy": 4.0,
                "kosdaq_foreign_net_buy": 5.0,
                "kosdaq_institutional_net_buy": 6.0,
            }

    class FakeKRX:
        def __init__(self, *_args, **_kwargs):
            pass

        def fetch_market_breadth(self, _target_date):
            return None

    fake_loader = FakeLoader()
    monkeypatch.setattr(macro_module, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}, "fred": {}, "krx": {}})
    monkeypatch.setattr(macro_module, "load_macro_series", lambda: [{"source": "FRED", "series_id": "DGS3", "name": "U.S. 3Y", "enabled": True}])
    monkeypatch.setattr(macro_module, "SupabaseLoader", lambda **_kwargs: fake_loader)
    monkeypatch.setattr(macro_module, "FREDCollector", FakeFRED)
    monkeypatch.setattr(macro_module, "compute_market_breadth_from_prices", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.collectors.krx_collector.KRXCollector", FakeKRX)
    monkeypatch.setattr("src.collectors.global_index_collector.GlobalIndexCollector", FakeGlobalIndex)

    macro_module.run_pipeline(__import__("datetime").date(2026, 5, 4), dry_run=False)
    global_rows = [records for table, records in fake_loader.upserts if table == "normalized_global_macro_daily"]
    assert global_rows
    assert global_rows[0][0]["us3y"] == 3.11
    normalized_series_rows = [records for table, records in fake_loader.upserts if table == "normalized_macro_series"]
    assert normalized_series_rows


def test_us3y_sql_file_exists():
    with open("sql/add_us3y_to_global_macro.sql", "r", encoding="utf-8") as handle:
        sql = handle.read()
    assert "ADD COLUMN IF NOT EXISTS us3y NUMERIC" in sql
