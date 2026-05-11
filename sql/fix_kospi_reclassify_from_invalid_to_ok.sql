-- fix_kospi_reclassify_from_invalid_to_ok.sql
-- Reclassifies KOSPI rows that were incorrectly marked INVALID due to a
-- hardcoded upper-bound range check (e.g., 1000~6500).  Any row where the
-- raw_value stored in quality_detail is a positive number from KIS's official
-- index API is a legitimate market value and should be OK.

-- ============================================================
-- STEP 1: Preview rows that will be reclassified (SELECT only)
-- ============================================================
select
  base_date,
  observed_at,
  series_id,
  value,
  source,
  source_symbol,
  quality_flag,
  quality_detail,
  change_rate
from public.normalized_macro_intraday
where series_id = 'KOSPI'
  and source = 'KIS'
  and quality_flag = 'INVALID'
  and (quality_detail->>'raw_value') is not null
  and (quality_detail->>'raw_value')::numeric > 0
order by observed_at desc;

-- ============================================================
-- STEP 2: Update — promote INVALID → OK, restore raw_value → value
-- ============================================================
update public.normalized_macro_intraday
set
  value        = (quality_detail->>'raw_value')::numeric,
  quality_flag = 'OK',
  quality_detail = jsonb_set(
    quality_detail - 'raw_value',
    '{reclassified_from}',
    '"INVALID_RANGE_RULE"'
  )
where series_id = 'KOSPI'
  and source = 'KIS'
  and quality_flag = 'INVALID'
  and (quality_detail->>'raw_value') is not null
  and (quality_detail->>'raw_value')::numeric > 0
  and abs(coalesce(change_rate, 0)) <= 20;

-- Rows where change_rate IS NULL (we can't verify it, but KIS source + positive
-- raw_value is enough — mark OK and note the reclassification reason)
update public.normalized_macro_intraday
set
  value        = (quality_detail->>'raw_value')::numeric,
  quality_flag = 'OK',
  quality_detail = jsonb_set(
    quality_detail - 'raw_value',
    '{reclassified_from}',
    '"INVALID_RANGE_RULE_NO_CHANGE_RATE"'
  )
where series_id = 'KOSPI'
  and source = 'KIS'
  and quality_flag = 'INVALID'
  and (quality_detail->>'raw_value') is not null
  and (quality_detail->>'raw_value')::numeric > 0
  and change_rate is null;
