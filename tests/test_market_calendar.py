from __future__ import annotations

from datetime import date

import pandas as pd

from src.collectors import market_calendar_collector as collector_module
from src.jobs import run_monthly_market_calendar_pipeline as pipeline_module
from src.utils import trading_calendar as calendar_utils


class _FakeCalendar:
    def schedule(self, start_date, end_date):
        index = pd.DatetimeIndex([pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")])
        return pd.DataFrame(
            {
                "market_open": [
                    pd.Timestamp("2026-01-02 09:00:00", tz="Asia/Seoul"),
                    pd.Timestamp("2026-01-05 09:00:00", tz="Asia/Seoul"),
                ],
                "market_close": [
                    pd.Timestamp("2026-01-02 15:30:00", tz="Asia/Seoul"),
                    pd.Timestamp("2026-01-05 15:30:00", tz="Asia/Seoul"),
                ],
            },
            index=index,
        )


def test_collector_returns_every_date(monkeypatch):
    monkeypatch.setattr(collector_module, "resolve_market_calendar_name", lambda _exchange: "XKRX")
    monkeypatch.setattr(collector_module.pmc, "get_calendar", lambda _name: _FakeCalendar())
    rows = collector_module.fetch_market_calendar("XKRX", date(2026, 1, 1), date(2026, 1, 5))
    assert len(rows) == 5
    assert rows[0]["calendar_date"] == "2026-01-01"
    assert rows[-1]["calendar_date"] == "2026-01-05"


def test_weekend_and_holiday_reason(monkeypatch):
    monkeypatch.setattr(collector_module, "resolve_market_calendar_name", lambda _exchange: "XKRX")
    monkeypatch.setattr(collector_module.pmc, "get_calendar", lambda _name: _FakeCalendar())
    rows = collector_module.fetch_market_calendar("XKRX", date(2026, 1, 1), date(2026, 1, 4))
    mapped = {row["calendar_date"]: row for row in rows}
    assert mapped["2026-01-02"]["is_open"] is True
    assert mapped["2026-01-03"]["is_open"] is False
    assert mapped["2026-01-03"]["reason"] == "weekend"
    assert mapped["2026-01-01"]["is_open"] is False
    assert mapped["2026-01-01"]["reason"] == "holiday"


class _Loader:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.upserts = []
        self.logs = []
        self.client = self

    def table(self, _name):
        if self.fail:
            raise RuntimeError("missing table")
        return self

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows})()

    def fetch_all(self, _table, _date_col, start_date, end_date, **_kwargs):
        if self.fail:
            raise RuntimeError("missing table")
        return [
            row for row in self.rows
            if start_date <= row["calendar_date"] <= end_date
        ]

    def upsert_records(self, table, records, **_kwargs):
        self.upserts.append((table, records))
        return True

    def insert_log(self, *args):
        self.logs.append(args)


def test_get_previous_and_next_trading_day():
    loader = _Loader(
        rows=[
            {"calendar_date": "2026-01-02", "exchange_code": "XKRX", "is_open": True},
            {"calendar_date": "2026-01-03", "exchange_code": "XKRX", "is_open": False},
            {"calendar_date": "2026-01-04", "exchange_code": "XKRX", "is_open": False},
            {"calendar_date": "2026-01-05", "exchange_code": "XKRX", "is_open": True},
        ]
    )
    assert calendar_utils.get_previous_trading_day(loader, date(2026, 1, 5)) == date(2026, 1, 2)
    assert calendar_utils.get_next_trading_day(loader, date(2026, 1, 2)) == date(2026, 1, 5)


def test_weekday_fallback_warning(monkeypatch):
    loader = _Loader(fail=True)
    calendar_utils._warn_once.cache_clear()
    messages = []
    monkeypatch.setattr(calendar_utils.logger, "warning", lambda message: messages.append(message))
    assert calendar_utils.is_trading_day(loader, date(2026, 1, 5)) is True
    assert any("CALENDAR_FALLBACK_USED" in message for message in messages)


def test_dry_run_pipeline_skips_write(monkeypatch):
    loader = _Loader()
    monkeypatch.setattr(pipeline_module, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(pipeline_module, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(pipeline_module, "resolve_market_calendar_name", lambda exchange: exchange)
    monkeypatch.setattr(
        pipeline_module,
        "fetch_market_calendar",
        lambda exchange, _start_date, _end_date: [
            {
                "calendar_date": "2026-01-01",
                "exchange_code": exchange,
                "market": "KRX" if exchange == "XKRX" else "US",
                "is_open": False,
                "open_time": None,
                "close_time": None,
                "timezone": "Asia/Seoul",
                "holiday_name": None,
                "reason": "holiday",
                "source": "pandas_market_calendars",
                "calendar_version": "test",
                "collected_at": "2026-01-01T00:00:00+09:00",
                "updated_at": "2026-01-01T00:00:00+09:00",
            }
        ],
    )
    summary = pipeline_module.run_pipeline(
        date(2026, 1, 1),
        date(2026, 1, 1),
        exchanges=["XKRX", "XNYS"],
        dry_run=True,
    )
    assert summary["dry_run"] is True
    assert len(summary["exchanges"]) == 2
    assert loader.upserts == []


def test_pipeline_table_missing_raises(monkeypatch):
    loader = _Loader(fail=True)
    monkeypatch.setattr(pipeline_module, "load_config", lambda: {"supabase": {"url": "x", "service_role_key": "y"}})
    monkeypatch.setattr(pipeline_module, "SupabaseLoader", lambda **_kwargs: loader)
    monkeypatch.setattr(pipeline_module, "resolve_market_calendar_name", lambda exchange: exchange)
    monkeypatch.setattr(pipeline_module, "fetch_market_calendar", lambda *_args, **_kwargs: [])
    try:
        pipeline_module.run_pipeline(date(2026, 1, 1), date(2026, 1, 1), exchanges=["XKRX"], dry_run=False)
    except RuntimeError as exc:
        assert "sql/add_market_trading_calendar.sql" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when market_trading_calendar is missing")
