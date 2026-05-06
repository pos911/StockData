from __future__ import annotations

import asyncio
from datetime import date

from src.jobs import run_daily_derivatives_pipeline as derivatives_pipeline
from src.jobs import run_daily_feature_pipeline as feature_pipeline
from src.jobs import run_daily_macro_pipeline as macro_pipeline
from src.jobs import run_daily_master_pipeline as master_pipeline
from src.jobs import run_daily_ranking_pipeline as ranking_pipeline
from src.jobs import run_daily_stock_pipeline as stock_pipeline
from src.utils.market_data_quality import is_valid_price_row
from src.utils import trading_calendar as calendar_utils


class _Loader:
    def __init__(self):
        self.logs = []

    def insert_log(self, *args):
        self.logs.append(args)


def test_should_skip_market_job_closed():
    class Loader:
        def fetch_all(self, *_args, **_kwargs):
            return [{"calendar_date": "2026-05-05", "exchange_code": "XKRX", "is_open": False, "reason": "holiday"}]

    skip, reason = calendar_utils.should_skip_market_job(Loader(), date(2026, 5, 5), "XKRX", "test_job")
    assert skip is True
    assert "MARKET_CLOSED" in reason


def test_should_not_skip_market_job_open():
    class Loader:
        def fetch_all(self, *_args, **_kwargs):
            return [{"calendar_date": "2026-05-06", "exchange_code": "XKRX", "is_open": True, "reason": "trading_day"}]

    skip, reason = calendar_utils.should_skip_market_job(Loader(), date(2026, 5, 6), "XKRX", "test_job")
    assert skip is False
    assert "MARKET_OPEN" in reason


def test_calendar_missing_uses_weekday_fallback_warning(monkeypatch):
    class Loader:
        def fetch_all(self, *_args, **_kwargs):
            raise RuntimeError("missing table")

    calendar_utils._warn_once.cache_clear()
    messages = []
    monkeypatch.setattr(calendar_utils.logger, "warning", lambda message: messages.append(message))
    skip, reason = calendar_utils.should_skip_market_job(Loader(), date(2026, 5, 9), "XKRX", "test_job")
    assert skip is True
    assert "weekday_fallback" in reason
    assert any("CALENDAR_FALLBACK_USED" in message for message in messages)


def test_ranking_pipeline_closed_market_skips_and_logs(monkeypatch):
    loader = _Loader()
    monkeypatch.setattr(ranking_pipeline, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(ranking_pipeline, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(ranking_pipeline, "should_skip_market_job", lambda *_args, **_kwargs: (True, "MARKET_CLOSED: XKRX 2026-05-05 holiday"))
    monkeypatch.setattr(ranking_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 4))
    asyncio.run(ranking_pipeline.run_pipeline(date(2026, 5, 5), dry_run=False))
    assert loader.logs[0][2] == "SKIPPED_MARKET_CLOSED"


def test_master_pipeline_closed_market_skips_and_logs(monkeypatch):
    loader = _Loader()
    monkeypatch.setattr(master_pipeline, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(master_pipeline, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(master_pipeline, "should_skip_market_job", lambda *_args, **_kwargs: (True, "MARKET_CLOSED: XKRX 2026-05-05 holiday"))
    monkeypatch.setattr(master_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 4))
    master_pipeline.run_pipeline(date(2026, 5, 5))
    assert loader.logs[0][2] == "SKIPPED_MARKET_CLOSED"


def test_stock_pipeline_closed_market_skips_and_logs(monkeypatch):
    loader = _Loader()
    monkeypatch.setattr(stock_pipeline, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(stock_pipeline, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(stock_pipeline, "should_skip_market_job", lambda *_args, **_kwargs: (True, "MARKET_CLOSED: XKRX 2026-05-05 holiday"))
    monkeypatch.setattr(stock_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 4))
    asyncio.run(stock_pipeline.run_pipeline(date(2026, 5, 5), limit=None))
    assert loader.logs[0][2] == "SKIPPED_MARKET_CLOSED"
    assert loader.logs[1][2] == "SKIPPED_MARKET_CLOSED"


def test_derivatives_pipeline_closed_market_skips_and_logs(monkeypatch):
    loader = _Loader()
    monkeypatch.setattr(derivatives_pipeline, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(derivatives_pipeline, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(derivatives_pipeline, "should_skip_market_job", lambda *_args, **_kwargs: (True, "MARKET_CLOSED: XKRX 2026-05-05 holiday"))
    monkeypatch.setattr(derivatives_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 4))
    derivatives_pipeline.run_pipeline(date(2026, 5, 5))
    assert loader.logs[0][2] == "SKIPPED_MARKET_CLOSED"


def test_us_equity_guardrail_carries_previous_day(monkeypatch):
    class Loader:
        pass

    class Collector:
        def fetch_daily_indices(self, target_date, skip_fields=None):
            if target_date == date(2026, 7, 3):
                return {"base_date": "2026-07-03", "sp500": 6000, "nasdaq": 19000, "sox": 5000, "vix": 17}
            return {"base_date": "2026-07-04", "sp500": None, "nasdaq": None, "sox": None, "vix": None}

    monkeypatch.setattr(macro_pipeline, "should_skip_market_job", lambda *_args, **_kwargs: (True, "MARKET_CLOSED: XNYS 2026-07-04 holiday"))
    monkeypatch.setattr(macro_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 7, 3))
    data, diagnostic = macro_pipeline._apply_us_equity_market_guardrail(
        Loader(),
        Collector(),
        {"base_date": "2026-07-04", "sp500": None, "nasdaq": None, "sox": None, "vix": None},
        date(2026, 7, 4),
    )
    assert data["sp500"] == 6000
    assert diagnostic["us_equity_market_closed"] is True
    assert diagnostic["us_equity_market_data_date"] == "2026-07-03"


def test_fred_ecos_macro_job_not_globally_blocked(monkeypatch):
    called = []

    def fake_should_skip(*_args, **_kwargs):
        called.append(True)
        return True, "MARKET_CLOSED: XNYS 2026-07-04 holiday"

    monkeypatch.setattr(macro_pipeline, "should_skip_market_job", fake_should_skip)
    monkeypatch.setattr(macro_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 7, 3))

    class Collector:
        def fetch_daily_indices(self, target_date, skip_fields=None):
            return {"base_date": target_date.strftime("%Y-%m-%d"), "sp500": None, "nasdaq": None, "sox": None, "vix": None}

    data, diagnostic = macro_pipeline._apply_us_equity_market_guardrail(
        loader=object(),
        global_index=Collector(),
        global_data={"base_date": "2026-07-04", "dxy": 100.0},
        target_date=date(2026, 7, 4),
    )
    assert called
    assert data["dxy"] == 100.0
    assert diagnostic["us_equity_market_closed"] is True


def test_zero_volume_row_is_not_valid_price():
    assert is_valid_price_row({"close_price": 232500, "volume": 0, "trading_value": 0}, market_is_open=True) is False


def test_feature_pipeline_skips_on_insufficient_valid_prices(monkeypatch):
    class Loader:
        def __init__(self):
            self.logs = []

        def fetch_all(self, table_name, *_args, **_kwargs):
            if table_name == "normalized_stock_prices_daily":
                return [{"symbol": "005930", "base_date": "2026-05-06", "close_price": 232500, "volume": 0, "trading_value": 0}]
            if table_name == "stocks_master":
                return [{"symbol": "005930", "market": "KOSPI"}]
            return []

        def insert_log(self, *args):
            self.logs.append(args)

    loader = Loader()
    monkeypatch.setattr(feature_pipeline, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(feature_pipeline, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(feature_pipeline, "is_market_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(feature_pipeline, "get_previous_trading_day", lambda *_args, **_kwargs: date(2026, 5, 4))
    status, processed = feature_pipeline.run_feature_pipeline(date(2026, 5, 6))
    assert status == "SKIPPED_INSUFFICIENT_PRICE_DATA"
    assert processed == 0
    assert loader.logs[0][2] == "SKIPPED_INSUFFICIENT_PRICE_DATA"
