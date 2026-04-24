WITH
freshness AS (
    SELECT
        table_name,
        latest_date,
        CASE
            WHEN latest_date IS NULL THEN NULL
            ELSE (CURRENT_DATE - latest_date)
        END AS lag_days
    FROM (
        SELECT 'normalized_global_macro_daily' AS table_name, MAX(base_date) AS latest_date FROM normalized_global_macro_daily
        UNION ALL
        SELECT 'market_breadth_daily', MAX(base_date) FROM market_breadth_daily
        UNION ALL
        SELECT 'normalized_derivatives_daily', MAX(base_date) FROM normalized_derivatives_daily
        UNION ALL
        SELECT 'normalized_stock_prices_daily', MAX(base_date) FROM normalized_stock_prices_daily
        UNION ALL
        SELECT 'normalized_stock_supply_daily', MAX(base_date) FROM normalized_stock_supply_daily
        UNION ALL
        SELECT 'normalized_stock_short_selling', MAX(base_date) FROM normalized_stock_short_selling
        UNION ALL
        SELECT 'normalized_stock_fundamentals_ratios', MAX(base_date) FROM normalized_stock_fundamentals_ratios
        UNION ALL
        SELECT 'normalized_stock_events_daily', MAX(base_date) FROM normalized_stock_events_daily
        UNION ALL
        SELECT 'normalized_macro_series', MAX(base_date) FROM normalized_macro_series
        UNION ALL
        SELECT 'feature_store_daily', MAX(base_date) FROM feature_store_daily
    ) t
),
latest_global AS (
    SELECT
        base_date,
        usdkrw,
        dxy,
        us10y,
        kr10y,
        kospi,
        kospi_change_rate,
        kosdaq,
        kosdaq_change_rate,
        nasdaq,
        nasdaq_change_rate,
        sp500,
        sp500_change_rate,
        sox,
        vix,
        wti,
        brent,
        gold,
        copper,
        bdry,
        hy_spread,
        kospi_individual_net_buy,
        kospi_foreign_net_buy,
        kospi_institutional_net_buy,
        kosdaq_individual_net_buy,
        kosdaq_foreign_net_buy,
        kosdaq_institutional_net_buy,
        available_at,
        updated_at
    FROM normalized_global_macro_daily
    ORDER BY base_date DESC
    LIMIT 1
),
latest_breadth AS (
    SELECT
        base_date,
        advances,
        declines,
        unchanged,
        advancing_volume,
        declining_volume,
        available_at,
        updated_at
    FROM market_breadth_daily
    ORDER BY base_date DESC
    LIMIT 1
),
latest_derivatives AS (
    SELECT
        base_date,
        kospi200_futures,
        futures_basis,
        open_interest,
        night_futures_return,
        expiration_flag,
        available_at,
        updated_at
    FROM normalized_derivatives_daily
    ORDER BY base_date DESC
    LIMIT 1
),
latest_feature_summary AS (
    SELECT
        base_date,
        COUNT(*) AS total_feature_rows,
        COUNT(DISTINCT symbol) AS symbol_count,
        COUNT(DISTINCT feature_name) AS feature_name_count
    FROM feature_store_daily
    WHERE base_date = (SELECT MAX(base_date) FROM feature_store_daily)
    GROUP BY base_date
),
latest_price_date AS (
    SELECT MAX(base_date) AS base_date
    FROM normalized_stock_prices_daily
),
latest_supply_date AS (
    SELECT MAX(base_date) AS base_date
    FROM normalized_stock_supply_daily
),
sample_symbols AS (
    SELECT *
    FROM (
        VALUES
            ('005930'),
            ('000660'),
            ('035420'),
            ('015760'),
            ('069960')
    ) AS v(symbol)
),
sample_stock_rows AS (
    SELECT
        ss.symbol,
        sm.name,
        sm.market,
        p.base_date AS price_date,
        p.close_price,
        p.volume,
        p.trading_value,
        p.outstanding_shares,
        s.base_date AS supply_date,
        s.individual_net_buy,
        s.foreign_net_buy,
        s.institutional_net_buy,
        s.foreign_holding_ratio,
        r.per,
        r.pbr,
        r.roe,
        r.debt_ratio
    FROM sample_symbols ss
    LEFT JOIN stocks_master sm
        ON sm.symbol = ss.symbol
    LEFT JOIN latest_price_date lpd
        ON TRUE
    LEFT JOIN normalized_stock_prices_daily p
        ON p.symbol = ss.symbol
       AND p.base_date = lpd.base_date
    LEFT JOIN latest_supply_date lsd
        ON TRUE
    LEFT JOIN normalized_stock_supply_daily s
        ON s.symbol = ss.symbol
       AND s.base_date = lsd.base_date
    LEFT JOIN normalized_stock_fundamentals_ratios r
        ON r.symbol = ss.symbol
       AND r.base_date = p.base_date
),
pipeline_health AS (
    SELECT
        target_date,
        job_name,
        status,
        COUNT(*) AS occurrences,
        MAX(records_processed) AS max_records_processed
    FROM pipeline_run_logs
    WHERE target_date >= CURRENT_DATE - 7
    GROUP BY target_date, job_name, status
    ORDER BY target_date DESC, job_name, status
),
quality_checks AS (
    SELECT
        jsonb_build_object(
            'global_macro_report_fields_present',
            (
                SELECT jsonb_build_object(
                    'base_date', base_date,
                    'has_kr10y', kr10y IS NOT NULL,
                    'has_kospi', kospi IS NOT NULL,
                    'has_kospi_change_rate', kospi_change_rate IS NOT NULL,
                    'has_kosdaq', kosdaq IS NOT NULL,
                    'has_kosdaq_change_rate', kosdaq_change_rate IS NOT NULL,
                    'has_nasdaq', nasdaq IS NOT NULL,
                    'has_nasdaq_change_rate', nasdaq_change_rate IS NOT NULL,
                    'has_sp500', sp500 IS NOT NULL,
                    'has_sp500_change_rate', sp500_change_rate IS NOT NULL,
                    'has_kospi_individual_net_buy', kospi_individual_net_buy IS NOT NULL,
                    'has_kospi_foreign_net_buy', kospi_foreign_net_buy IS NOT NULL,
                    'has_kospi_institutional_net_buy', kospi_institutional_net_buy IS NOT NULL,
                    'has_kosdaq_individual_net_buy', kosdaq_individual_net_buy IS NOT NULL,
                    'has_kosdaq_foreign_net_buy', kosdaq_foreign_net_buy IS NOT NULL,
                    'has_kosdaq_institutional_net_buy', kosdaq_institutional_net_buy IS NOT NULL
                )
                FROM latest_global
            ),
            'known_special_case_fields',
            jsonb_build_object(
                'normalized_global_macro_daily.kr10y_is_monthly_fred', true
            )
        ) AS payload
),
raw_counts AS (
    SELECT
        section,
        source,
        base_date,
        row_count
    FROM (
        SELECT
            'raw_stock_prices_daily' AS section,
            source,
            base_date,
            COUNT(*) AS row_count
        FROM raw_stock_prices_daily
        WHERE base_date = (SELECT MAX(base_date) FROM raw_stock_prices_daily)
        GROUP BY source, base_date

        UNION ALL

        SELECT
            'raw_stock_supply_daily' AS section,
            source,
            base_date,
            COUNT(*) AS row_count
        FROM raw_stock_supply_daily
        WHERE base_date = (SELECT MAX(base_date) FROM raw_stock_supply_daily)
        GROUP BY source, base_date

        UNION ALL

        SELECT
            'raw_macro_series' AS section,
            source,
            base_date,
            COUNT(*) AS row_count
        FROM raw_macro_series
        WHERE base_date >= (SELECT MAX(base_date) - INTERVAL '5 days' FROM raw_macro_series)
        GROUP BY source, base_date
    ) x
    ORDER BY section, base_date DESC, source
)
SELECT jsonb_pretty(
    jsonb_build_object(
        'freshness', COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'table_name', table_name,
                        'latest_date', latest_date,
                        'lag_days', lag_days
                    )
                    ORDER BY table_name
                )
                FROM freshness
            ),
            '[]'::jsonb
        ),
        'latest_global_macro', COALESCE(
            (SELECT to_jsonb(latest_global) FROM latest_global),
            '{}'::jsonb
        ),
        'latest_market_breadth', COALESCE(
            (SELECT to_jsonb(latest_breadth) FROM latest_breadth),
            '{}'::jsonb
        ),
        'latest_derivatives', COALESCE(
            (SELECT to_jsonb(latest_derivatives) FROM latest_derivatives),
            '{}'::jsonb
        ),
        'sample_stock_rows', COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(sample_stock_rows) ORDER BY symbol)
                FROM sample_stock_rows
            ),
            '[]'::jsonb
        ),
        'feature_summary', COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(latest_feature_summary))
                FROM latest_feature_summary
            ),
            '[]'::jsonb
        ),
        'pipeline_health', COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(pipeline_health))
                FROM pipeline_health
            ),
            '[]'::jsonb
        ),
        'raw_counts', COALESCE(
            (
                SELECT jsonb_agg(to_jsonb(raw_counts))
                FROM raw_counts
            ),
            '[]'::jsonb
        ),
        'quality_checks', COALESCE(
            (SELECT payload FROM quality_checks),
            '{}'::jsonb
        )
    )
) AS verification_report;
