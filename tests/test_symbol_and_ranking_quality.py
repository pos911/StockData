import io
from contextlib import redirect_stdout

from scripts.verify_data import _print_symbol_quality
from src.jobs.run_daily_stock_pipeline import _infer_asset_type, _standardize_market
from src.utils.dynamic_universe_loader import DynamicUniverseLoader
from src.utils.symbols import normalize_symbol_value


class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _FakeTable:
    def __init__(self, data):
        self._data = data

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResult(self._data)


class _FakeClient:
    def __init__(self, table_map):
        self._table_map = table_map

    def table(self, name):
        return _FakeTable(self._table_map.get(name, []))


class _FakeLoader:
    def __init__(self, table_map=None):
        self.client = _FakeClient(table_map or {})
        self.upserts = []

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True


def test_normalize_symbol_value_rules():
    assert normalize_symbol_value("Q530036") == "530036"
    assert normalize_symbol_value("Q550043") == "550043"
    assert normalize_symbol_value("530036") == "530036"
    assert normalize_symbol_value("0000Z0") == "0000Z0"
    assert normalize_symbol_value("0167Z0") == "0167Z0"
    assert normalize_symbol_value("5930") == "005930"


def test_plus_prefix_etf_but_yg_plus_is_not():
    assert _standardize_market("KOSPI", "PLUS Hanwha Group") == "ETF"
    assert _infer_asset_type("PLUS Hanwha Group", "KOSPI") == "ETF"
    assert _standardize_market("KOSPI", "YG PLUS") == "KOSPI"
    assert _infer_asset_type("YG PLUS", "KOSPI") == "STOCK"


def test_persist_rankings_filters_to_requested_master_market():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.loader = _FakeLoader(
        {
            "stocks_master": [
                {"symbol": "005930", "name": "Samsung Electronics", "market": "KOSPI", "asset_type": "STOCK"},
                {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "asset_type": "ETF"},
                {"symbol": "058470", "name": "LEENO Industrial", "market": "KOSDAQ", "asset_type": "STOCK"},
            ]
        }
    )

    rows = [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "Samsung Electronics", "acml_vol": "100", "acml_tr_pbmn": "1000"},
        {"mksc_shrn_iscd": "069500", "hts_kor_isnm": "KODEX 200", "acml_vol": "200", "acml_tr_pbmn": "2000"},
        {"mksc_shrn_iscd": "058470", "hts_kor_isnm": "LEENO Industrial", "acml_vol": "300", "acml_tr_pbmn": "3000"},
    ]

    persisted = loader._persist_rankings(rows, "KOSPI", "volume", 30)
    assert persisted == [{"code": "005930", "name": "Samsung Electronics", "market": "KOSPI"}]

    normalized = next(records for table, records in loader.loader.upserts if table == "normalized_market_rankings_daily")
    assert len(normalized) == 1
    assert normalized[0]["symbol"] == "005930"
    assert normalized[0]["market"] == "KOSPI"


def test_valid_price_fallback_builds_missing_rankings():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    latest_date = "2026-05-03"
    valid_rows = [
        {"symbol": "005930", "volume": 100, "trading_value": 1000, "close_price": 1, "market_cap": 5000},
        {"symbol": "058470", "volume": 200, "trading_value": 3000, "close_price": 1, "market_cap": 9000},
        {"symbol": "069500", "volume": 150, "trading_value": 2500, "close_price": 1, "market_cap": 7000},
    ]
    master_map = {
        "005930": {"symbol": "005930", "name": "Samsung Electronics", "market": "KOSPI", "asset_type": "STOCK"},
        "058470": {"symbol": "058470", "name": "LEENO Industrial", "market": "KOSDAQ", "asset_type": "STOCK"},
        "069500": {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "asset_type": "ETF"},
    }

    kosdaq_volume = loader._build_fallback_rankings_from_valid_prices(latest_date, valid_rows, master_map, "KOSDAQ", "volume", 30)
    kosdaq_trading = loader._build_fallback_rankings_from_valid_prices(latest_date, valid_rows, master_map, "KOSDAQ", "trading_value", 30)
    kosdaq_cap = loader._build_fallback_rankings_from_valid_prices(latest_date, valid_rows, master_map, "KOSDAQ", "market_cap", 30)
    etf_trading = loader._build_fallback_rankings_from_valid_prices(latest_date, valid_rows, master_map, "ETF", "trading_value", 20)

    assert [row["symbol"] for row in kosdaq_volume] == ["058470"]
    assert [row["symbol"] for row in kosdaq_trading] == ["058470"]
    assert [row["symbol"] for row in kosdaq_cap] == ["058470"]
    assert [row["symbol"] for row in etf_trading] == ["069500"]


def test_cleanup_sql_mentions_better_q_prefix_price_merge():
    with open("sql/cleanup_symbol_market_quality.sql", "r", encoding="utf-8") as handle:
        sql = handle.read()
    assert "quality_score" in sql
    assert "COALESCE(trading_value, 0)" in sql
    assert "UPDATE public.normalized_stock_prices_daily c" in sql
    assert "DELETE FROM public.normalized_stock_prices_daily" in sql


def test_verify_data_flags_q_prefix_symbols_as_failure():
    loader = _FakeLoader(
        {
            "feature_store_daily": [{"symbol": "Q530036"}],
            "normalized_stock_prices_daily": [],
            "raw_stock_prices_daily": [],
            "normalized_stock_supply_daily": [],
            "raw_stock_supply_daily": [],
            "normalized_stock_short_selling": [],
            "normalized_stock_snapshots_daily": [],
            "normalized_market_rankings_daily": [],
            "raw_market_rankings": [],
        }
    )
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _print_symbol_quality(loader)
    output = buffer.getvalue()
    assert "FAIL_SYMBOL_NORMALIZATION" in output
    assert "Q530036" in output
