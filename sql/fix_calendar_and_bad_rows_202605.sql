-- Manual review script for correcting market calendar and bad market rows around 2026-05.
-- Review each SELECT first, then execute UPDATE/DELETE statements intentionally.
-- This script does NOT touch macro tables.

-- 1) Diagnose current XKRX calendar rows around the affected period
select
  calendar_date,
  exchange_code,
  is_open,
  reason,
  source,
  holiday_name,
  open_time,
  close_time,
  updated_at
from public.market_trading_calendar
where exchange_code = 'XKRX'
  and calendar_date between date '2026-05-03' and date '2026-05-08'
order by calendar_date;

-- 2) Apply manual override for 2026-05-06 XKRX trading day
update public.market_trading_calendar
set
  is_open = true,
  open_time = '2026-05-06 09:00:00+09',
  close_time = '2026-05-06 15:30:00+09',
  holiday_name = null,
  reason = 'manual_override_trading_day',
  source = 'manual_override',
  updated_at = now()
where exchange_code = 'XKRX'
  and calendar_date = date '2026-05-06';

-- 3) Diagnose bad Korean market price rows for 2026-05-06
select
  p.symbol,
  m.market,
  p.base_date,
  p.close_price,
  p.volume,
  p.trading_value,
  p.market_cap,
  p.outstanding_shares,
  p.updated_at
from public.normalized_stock_prices_daily p
join public.stocks_master m
  on p.symbol = m.symbol
where p.base_date = date '2026-05-06'
  and m.market in ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
order by m.market, p.symbol;

-- 4) Delete invalid Korean market price rows for 2026-05-06
delete from public.normalized_stock_prices_daily p
using public.stocks_master m
where p.symbol = m.symbol
  and p.base_date = date '2026-05-06'
  and m.market in ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  and (
    coalesce(p.volume, 0) <= 0
    or coalesce(p.trading_value, 0) <= 0
  );

-- 5) Diagnose feature rows for 2026-05-06 before cleanup
select
  base_date,
  symbol,
  feature_name,
  feature_value,
  updated_at
from public.feature_store_daily
where base_date = date '2026-05-06'
order by symbol, feature_name;

-- 6) Delete feature rows generated for 2026-05-06
delete from public.feature_store_daily
where base_date = date '2026-05-06';

-- 7) Diagnose closed-date ranking rows over recent 60 days
select
  r.base_date,
  r.market,
  r.rank_type,
  r.source,
  count(*) as row_count
from public.normalized_market_rankings_daily r
join public.market_trading_calendar c
  on c.calendar_date = r.base_date
 and c.exchange_code = 'XKRX'
where c.is_open = false
  and coalesce(c.source, '') <> 'manual_override'
  and r.market in ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  and r.base_date >= current_date - interval '60 days'
group by r.base_date, r.market, r.rank_type, r.source
order by r.base_date desc, r.market, r.rank_type, r.source;

-- 8) Delete normalized closed-date ranking rows
delete from public.normalized_market_rankings_daily r
using public.market_trading_calendar c
where c.calendar_date = r.base_date
  and c.exchange_code = 'XKRX'
  and c.is_open = false
  and coalesce(c.source, '') <> 'manual_override'
  and r.market in ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  and r.base_date >= current_date - interval '60 days';

-- 9) Delete raw closed-date ranking rows
delete from public.raw_market_rankings r
using public.market_trading_calendar c
where c.calendar_date = r.base_date
  and c.exchange_code = 'XKRX'
  and c.is_open = false
  and coalesce(c.source, '') <> 'manual_override'
  and r.market in ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  and r.base_date >= current_date - interval '60 days';

-- 10) Re-check corrected calendar rows
select
  calendar_date,
  exchange_code,
  is_open,
  reason,
  source,
  holiday_name,
  open_time,
  close_time,
  updated_at
from public.market_trading_calendar
where exchange_code = 'XKRX'
  and calendar_date between date '2026-05-03' and date '2026-05-08'
order by calendar_date;
