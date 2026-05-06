from __future__ import annotations

import pytest

from src.jobs import run_daily_kis_universe_pipeline as kis_universe_module
from src.utils.dynamic_universe_loader import DynamicUniverseLoader


@pytest.mark.asyncio
async def test_kis_universe_loader_combines_sources_and_applies_limit():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.collector = object()
    loader.loader = None
    loader.config = {}

    loader._load_master_symbol_map = lambda: {
        "005930": {"symbol": "005930", "market": "KOSPI", "asset_type": "STOCK", "name": "Samsung"},
        "000660": {"symbol": "000660", "market": "KOSPI", "asset_type": "STOCK", "name": "SK hynix"},
        "058470": {"symbol": "058470", "market": "KOSDAQ", "asset_type": "STOCK", "name": "Leeno"},
    }
    loader._load_static_universe = lambda: [{"code": "005930", "name": "Samsung", "market": "KOSPI", "source_category": "static"}]
    loader._load_latest_kis_ranking_universe = lambda _master_map: [
        {"code": "000660", "name": "SK hynix", "market": "KOSPI", "source_category": "report_rank"}
    ]
    loader._load_latest_ranked_universe = lambda _master_map: []

    async def _live(_master_map):
        return [{"code": "058470", "name": "Leeno", "market": "KOSDAQ", "source_category": "kis_volume_rank"}]

    loader._load_live_kis_volume_rank_universe = _live

    universe = await DynamicUniverseLoader.get_kis_detail_universe(loader, requested_limit=2)
    assert len(universe) == 2
    assert universe[0]["symbol"] == "005930"
    assert universe[1]["symbol"] in {"000660", "058470"}


class _FakeLoader:
    def __init__(self):
        self.upserts = []
        self.logs = []

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True

    def insert_log(self, *args):
        self.logs.append(args)


@pytest.mark.asyncio
async def test_kis_universe_pipeline_stores_snapshot_when_price_is_invalid(monkeypatch):
    fake_loader = _FakeLoader()

    class FakeAuth:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    class FakeCollector:
        def __init__(self, *_args, **_kwargs):
            self.write_enabled = True

        async def fetch_kis_daily_price(self, *_args, **_kwargs):
            return {
                "symbol": "005930",
                "base_date": "2026-05-06",
                "close_price": 100,
                "volume": 0,
                "trading_value": 0,
            }

        async def fetch_kis_price_snapshot(self, *_args, **_kwargs):
            return {
                "symbol": "005930",
                "base_date": "2026-05-06",
                "market_cap": 1_000,
                "listed_shares": 10,
                "foreign_holding_ratio": 50.0,
                "per": 10.0,
                "pbr": 1.2,
                "w52_high": 120,
                "w52_low": 80,
                "source": "KIS_DETAIL",
            }

        async def fetch_kis_investment_ratios(self, *_args, **_kwargs):
            return {
                "symbol": "005930",
                "base_date": "2026-05-06",
                "per": 10.0,
                "pbr": 1.2,
                "roe": 8.0,
                "debt_ratio": 50.0,
                "source": "KIS_DETAIL",
                "available_at": "2026-05-06T16:00:00+09:00",
            }

    class FakeUniverseLoader:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_kis_detail_universe(self, requested_limit=None, include_live_kis_volume=True):
            del requested_limit, include_live_kis_volume
            return [{"symbol": "005930", "name": "Samsung", "market": "KOSPI", "source_category": "static"}]

    async def _close_session():
        return None

    monkeypatch.setattr(kis_universe_module, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}, "kis": {}})
    monkeypatch.setattr(kis_universe_module, "SupabaseLoader", lambda **_kwargs: fake_loader)
    monkeypatch.setattr(kis_universe_module, "KISAuthManager", FakeAuth)
    monkeypatch.setattr(kis_universe_module, "KISDomesticStockCollector", FakeCollector)
    monkeypatch.setattr(kis_universe_module, "DynamicUniverseLoader", FakeUniverseLoader)
    monkeypatch.setattr(kis_universe_module, "should_skip_market_job", lambda *_args, **_kwargs: (False, "MARKET_OPEN"))
    monkeypatch.setattr(kis_universe_module.KISBaseCollector, "close_session", staticmethod(_close_session))

    result = await kis_universe_module.run_pipeline(__import__("datetime").date(2026, 5, 6), limit=100, dry_run=False)
    assert result["kis_detail_success_count"] == 1
    assert result["kis_price_valid_count"] == 0
    assert result["kis_snapshot_success_count"] == 1
    assert result["kis_ratio_success_count"] == 1
    tables = [table for table, _records in fake_loader.upserts]
    assert "normalized_stock_snapshots_daily" in tables
    assert "normalized_stock_fundamentals_ratios" in tables


def test_kr_volume_ranking_never_uses_price_fallback_for_sparse_kis_rows():
    from src.jobs import run_daily_ranking_pipeline as ranking_module

    source, rows = ranking_module._select_volume_rankings(
        loader=None,
        target_date=__import__("datetime").date(2026, 5, 6),
        market="KOSPI",
        kis_rows=[{"symbol": "005930", "name": "Samsung", "volume": 10, "trading_value": 100, "metric_value": 10, "raw_data": {}}],
        master_map={},
        allow_price_fallback=True,
    )
    assert source == "KIS"
    assert len(rows) == 1
