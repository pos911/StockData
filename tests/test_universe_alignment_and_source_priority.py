from __future__ import annotations

import asyncio

import pandas as pd

from src.features.generate_features import FeatureGenerator
from src.utils.dynamic_universe_loader import DynamicUniverseLoader


class _StubLoader:
    client = object()


def test_apply_price_source_priority_prefers_kis_detail():
    generator = FeatureGenerator(_StubLoader())
    df = pd.DataFrame(
        [
            {"symbol": "005930", "base_date": "2026-05-08", "source": "KIS", "close_price": 100},
            {"symbol": "005930", "base_date": "2026-05-08", "source": "KIS_DETAIL", "close_price": 101},
            {"symbol": "005930", "base_date": "2026-05-07", "source": "UNKNOWN", "close_price": 99},
        ]
    )
    prioritized = generator._apply_price_source_priority(df)
    row = prioritized[prioritized["base_date"] == "2026-05-08"].iloc[0]
    assert row["source"] == "KIS_DETAIL"
    assert row["close_price"] == 101


def test_apply_price_source_priority_drops_unknown_when_better_source_exists():
    generator = FeatureGenerator(_StubLoader())
    df = pd.DataFrame(
        [
            {"symbol": "000660", "base_date": "2026-05-08", "source": "UNKNOWN", "close_price": 1},
            {"symbol": "000660", "base_date": "2026-05-08", "source": "KIS", "close_price": 2},
        ]
    )
    prioritized = generator._apply_price_source_priority(df)
    assert len(prioritized) == 1
    assert prioritized.iloc[0]["source"] == "KIS"


def test_kis_detail_universe_excludes_legacy_ranking_rows():
    loader = DynamicUniverseLoader.__new__(DynamicUniverseLoader)
    loader.collector = None
    loader.config = {}
    loader.static_universe_path = ""
    loader._load_master_symbol_map = lambda: {}
    loader._load_static_universe = lambda: [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "source_category": "static"}]
    loader._load_latest_kis_ranking_universe = lambda _master: [{"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "source_category": "report_rank"}]
    loader._load_live_kis_volume_rank_universe = lambda _master: [{"code": "278470", "name": "에이피알", "market": "KOSPI", "source_category": "kis_volume_rank"}]

    result = asyncio.run(loader.get_kis_detail_universe(requested_limit=100, include_live_kis_volume=False))
    symbols = {row["symbol"] for row in result}
    assert symbols == {"005930", "000660"}


def test_delete_recomputed_feature_rows_without_symbols_clears_all_non_global():
    class _DeleteQuery:
        def __init__(self):
            self.calls = []

        def delete(self):
            self.calls.append(("delete",))
            return self

        def eq(self, field, value):
            self.calls.append(("eq", field, value))
            return self

        def neq(self, field, value):
            self.calls.append(("neq", field, value))
            return self

        def in_(self, field, values):
            self.calls.append(("in", field, tuple(values)))
            return self

        def execute(self):
            self.calls.append(("execute",))
            return None

    query = _DeleteQuery()

    class _Client:
        def table(self, _name):
            return query

    class _Loader:
        client = _Client()

    generator = FeatureGenerator(_Loader())
    generator._delete_recomputed_feature_rows("2026-05-08")
    assert ("eq", "base_date", "2026-05-08") in query.calls
    assert ("neq", "symbol", "GLOBAL") in query.calls
