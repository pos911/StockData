from __future__ import annotations

from datetime import date

from src.features import generate_features
from src.jobs import run_daily_feature_pipeline as feature_pipeline


def test_generate_features_run_job_returns_int(monkeypatch):
    logs = []

    class FakeLoader:
        def insert_log(self, *args):
            logs.append(args)

    class FakeGenerator:
        def __init__(self, loader):
            self.loader = loader

        def generate_features_for_date(self, target_date):
            assert target_date == date(2026, 5, 8)
            return 180

    monkeypatch.setattr(
        generate_features,
        "load_config",
        lambda: {"supabase": {"url": "x", "service_role_key": "y"}},
    )
    monkeypatch.setattr(generate_features, "SupabaseLoader", lambda **_kwargs: FakeLoader())
    monkeypatch.setattr(generate_features, "FeatureGenerator", FakeGenerator)

    processed = generate_features.run_job(date(2026, 5, 8))
    assert isinstance(processed, int)
    assert processed == 180
    assert logs[0][2] == "SUCCESS"
    assert logs[0][3] == 180


def test_generate_features_run_job_coerces_none_to_zero(monkeypatch):
    logs = []

    class FakeLoader:
        def insert_log(self, *args):
            logs.append(args)

    class FakeGenerator:
        def __init__(self, loader):
            self.loader = loader

        def generate_features_for_date(self, _target_date):
            return None

    monkeypatch.setattr(
        generate_features,
        "load_config",
        lambda: {"supabase": {"url": "x", "service_role_key": "y"}},
    )
    monkeypatch.setattr(generate_features, "SupabaseLoader", lambda **_kwargs: FakeLoader())
    monkeypatch.setattr(generate_features, "FeatureGenerator", FakeGenerator)

    processed = generate_features.run_job(date(2026, 5, 8))
    assert processed == 0
    assert logs[0][2] == "WARN"
    assert logs[0][3] == 0


def test_run_feature_pipeline_none_return_does_not_raise(monkeypatch):
    class FakeLoader:
        def fetch_all(self, table_name, *_args, **_kwargs):
            if table_name == "normalized_stock_prices_daily":
                return [{"symbol": "005930", "close_price": 100, "volume": 10, "trading_value": 1000}]
            if table_name == "stocks_master":
                return [{"symbol": "005930", "market": "KOSPI"}]
            return []

        def insert_log(self, *_args):
            return None

    monkeypatch.setattr(
        feature_pipeline,
        "load_config",
        lambda: {"supabase": {"url": "x", "service_role_key": "y"}},
    )
    monkeypatch.setattr(feature_pipeline, "SupabaseLoader", lambda **_kwargs: FakeLoader())
    monkeypatch.setattr(feature_pipeline, "is_market_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(feature_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 7))

    import src.features.generate_features as generate_features_module

    monkeypatch.setattr(generate_features_module, "run_job", lambda _target_date: None)

    status, processed = feature_pipeline.run_feature_pipeline(date(2026, 5, 8))
    assert status == "WARN"
    assert processed == 0


def test_run_feature_pipeline_success_status_from_processed(monkeypatch):
    class FakeLoader:
        def fetch_all(self, table_name, *_args, **_kwargs):
            if table_name == "normalized_stock_prices_daily":
                return [{"symbol": "005930", "close_price": 100, "volume": 10, "trading_value": 1000}]
            if table_name == "stocks_master":
                return [{"symbol": "005930", "market": "KOSPI"}]
            return []

        def insert_log(self, *_args):
            return None

    monkeypatch.setattr(
        feature_pipeline,
        "load_config",
        lambda: {"supabase": {"url": "x", "service_role_key": "y"}},
    )
    monkeypatch.setattr(feature_pipeline, "SupabaseLoader", lambda **_kwargs: FakeLoader())
    monkeypatch.setattr(feature_pipeline, "is_market_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(feature_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 7))

    import src.features.generate_features as generate_features_module

    monkeypatch.setattr(generate_features_module, "run_job", lambda _target_date: 180)

    status, processed = feature_pipeline.run_feature_pipeline(date(2026, 5, 8))
    assert status == "SUCCESS"
    assert processed == 180


def test_run_feature_pipeline_zero_processed_is_warn(monkeypatch):
    class FakeLoader:
        def fetch_all(self, table_name, *_args, **_kwargs):
            if table_name == "normalized_stock_prices_daily":
                return [{"symbol": "005930", "close_price": 100, "volume": 10, "trading_value": 1000}]
            if table_name == "stocks_master":
                return [{"symbol": "005930", "market": "KOSPI"}]
            return []

        def insert_log(self, *_args):
            return None

    monkeypatch.setattr(
        feature_pipeline,
        "load_config",
        lambda: {"supabase": {"url": "x", "service_role_key": "y"}},
    )
    monkeypatch.setattr(feature_pipeline, "SupabaseLoader", lambda **_kwargs: FakeLoader())
    monkeypatch.setattr(feature_pipeline, "is_market_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(feature_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 7))

    import src.features.generate_features as generate_features_module

    monkeypatch.setattr(generate_features_module, "run_job", lambda _target_date: 0)

    status, processed = feature_pipeline.run_feature_pipeline(date(2026, 5, 8))
    assert status == "WARN"
    assert processed == 0
