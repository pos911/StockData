CREATE TABLE IF NOT EXISTS public.market_trading_calendar (
    calendar_date DATE NOT NULL,
    exchange_code VARCHAR(20) NOT NULL,
    market VARCHAR(30) NOT NULL,
    is_open BOOLEAN NOT NULL,
    open_time TIMESTAMP WITH TIME ZONE NULL,
    close_time TIMESTAMP WITH TIME ZONE NULL,
    timezone VARCHAR(50) NOT NULL DEFAULT 'Asia/Seoul',
    holiday_name TEXT NULL,
    reason TEXT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'pandas_market_calendars',
    calendar_version VARCHAR(50) NULL,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (calendar_date, exchange_code)
);

CREATE INDEX IF NOT EXISTS market_trading_calendar_exchange_date_idx
    ON public.market_trading_calendar (exchange_code, calendar_date);

CREATE INDEX IF NOT EXISTS market_trading_calendar_is_open_idx
    ON public.market_trading_calendar (exchange_code, is_open, calendar_date);

COMMENT ON TABLE public.market_trading_calendar
IS 'Daily trading calendar for each supported exchange. Stores open/closed status, schedule times, and holiday/weekend reasons.';

COMMENT ON COLUMN public.market_trading_calendar.is_open
IS 'True when the exchange is open for trading on calendar_date; false for weekends, holidays, or dates with no trading schedule.';

COMMENT ON COLUMN public.market_trading_calendar.open_time
IS 'Exchange-local market open time converted to timestamptz when a trading schedule exists.';

COMMENT ON COLUMN public.market_trading_calendar.close_time
IS 'Exchange-local market close time converted to timestamptz when a trading schedule exists.';

COMMENT ON COLUMN public.market_trading_calendar.reason
IS 'Reason code for the date classification: trading_day, weekend, holiday, or missing_schedule.';

COMMENT ON COLUMN public.market_trading_calendar.holiday_name
IS 'Holiday name when the calendar package exposes one; null when unavailable.';

COMMENT ON COLUMN public.market_trading_calendar.calendar_version
IS 'Collector package version or calendar build identifier used when generating rows.';

NOTIFY pgrst, 'reload schema';
