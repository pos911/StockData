SELECT jsonb_pretty(
    jsonb_build_object(
        'freshness',
        (
            SELECT jsonb_agg(row_data ORDER BY row_data->>'table_name')
            FROM (
                SELECT jsonb_build_object(
                    'table_name', table_name,
                    'latest_date', latest_date,
                    'lag_days', lag_days
                ) AS row_data
                FROM (
                    SELECT
                        table_name,
                        latest_date,
                        CASE
                            WHEN latest_date IS NULL THEN NULL
                            ELSE (CURRENT_DATE - latest_date)
                        END AS lag_days
                    FROM (
                        SELECT 'stocks_master' AS table_name, MAX(updated_at)::date AS latest_date FROM stocks_master
                        UNION ALL
                        SELECT 'macro_series_master', MAX(updated_at)::date FROM macro_series_master
                        UNION ALL
                        SELECT 'normalized_stock_prices_daily', MAX(base_date) FROM normalized_stock_prices_daily
                        UNION ALL
                        SELECT 'normalized_stock_supply_daily', MAX(base_date) FROM normalized_stock_supply_daily
                        UNION ALL
                        SELECT 'normalized_stock_fundamentals_ratios', MAX(base_date) FROM normalized_stock_fundamentals_ratios
                        UNION ALL
                        SELECT 'normalized_macro_series', MAX(base_date) FROM normalized_macro_series
                        UNION ALL
                        SELECT 'normalized_global_macro_daily', MAX(base_date) FROM normalized_global_macro_daily
                        UNION ALL
                        SELECT 'market_breadth_daily', MAX(base_date) FROM market_breadth_daily
                        UNION ALL
                        SELECT 'normalized_derivatives_daily', MAX(base_date) FROM normalized_derivatives_daily
                        UNION ALL
                        SELECT 'feature_store_daily', MAX(base_date) FROM feature_store_daily
                        UNION ALL
                        SELECT 'raw_ecos_macro_daily', MAX(date) FROM raw_ecos_macro_daily
                    ) freshness_base
                ) freshness_rows
            ) freshness_json
        ),
        'universe_summary',
        (
            SELECT jsonb_build_object(
                'active_count', COUNT(*) FILTER (WHERE is_active),
                'inactive_count', COUNT(*) FILTER (WHERE NOT is_active),
                'latest_updated_at', MAX(updated_at)
            )
            FROM stocks_master
        ),
        'latest_global_macro',
        (
            SELECT to_jsonb(x)
            FROM (
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
                    available_at
                FROM normalized_global_macro_daily
                ORDER BY base_date DESC
                LIMIT 1
            ) x
        ),
        'latest_market_breadth',
        (
            SELECT to_jsonb(x)
            FROM (
                SELECT
                    base_date,
                    advances,
                    declines,
                    unchanged,
                    advancing_volume,
                    declining_volume,
                    available_at
                FROM market_breadth_daily
                ORDER BY base_date DESC
                LIMIT 1
            ) x
        ),
        'latest_derivatives',
        (
            SELECT to_jsonb(x)
            FROM (
                SELECT
                    base_date,
                    kospi200_futures,
                    futures_basis,
                    open_interest,
                    night_futures_return,
                    expiration_flag,
                    available_at
                FROM normalized_derivatives_daily
                ORDER BY base_date DESC
                LIMIT 1
            ) x
        ),
        'ecos_status',
        (
            SELECT jsonb_build_object(
                'raw_latest_date', (SELECT MAX(date) FROM raw_ecos_macro_daily),
                'normalized_latest_date', (
                    SELECT MAX(base_date)
                    FROM normalized_macro_series
                    WHERE series_id IN ('KR_GOVT_10Y', 'USDKRW', 'KR_GOVT_3Y', 'KR_CORP_AA_3Y', 'KR_CORP_BBB_3Y')
                ),
                'latest_key_series', (
                    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.series_id)
                    FROM (
                        SELECT series_id, base_date, value
                        FROM normalized_macro_series
                        WHERE series_id IN ('KR_GOVT_10Y', 'USDKRW', 'KR_GOVT_3Y', 'KR_CORP_AA_3Y', 'KR_CORP_BBB_3Y')
                          AND base_date = (
                              SELECT MAX(base_date)
                              FROM normalized_macro_series
                              WHERE series_id IN ('KR_GOVT_10Y', 'USDKRW', 'KR_GOVT_3Y', 'KR_CORP_AA_3Y', 'KR_CORP_BBB_3Y')
                          )
                    ) x
                )
            )
        ),
        'sample_stock_rows',
        (
            SELECT jsonb_agg(to_jsonb(x) ORDER BY x.symbol)
            FROM (
                SELECT
                    ss.symbol,
                    sm.name,
                    sm.market,
                    sm.is_active,
                    p.base_date AS price_date,
                    p.close_price,
                    p.volume,
                    p.trading_value,
                    p.market_cap,
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
                FROM (
                    VALUES
                        ('005930'),
                        ('000660'),
                        ('035420'),
                        ('015760'),
                        ('069960')
                ) AS ss(symbol)
                LEFT JOIN stocks_master sm
                    ON sm.symbol = ss.symbol
                LEFT JOIN normalized_stock_prices_daily p
                    ON p.symbol = ss.symbol
                   AND p.base_date = (SELECT MAX(base_date) FROM normalized_stock_prices_daily)
                LEFT JOIN normalized_stock_supply_daily s
                    ON s.symbol = ss.symbol
                   AND s.base_date = (SELECT MAX(base_date) FROM normalized_stock_supply_daily)
                LEFT JOIN normalized_stock_fundamentals_ratios r
                    ON r.symbol = ss.symbol
                   AND r.base_date = p.base_date
            ) x
        ),
        'feature_checks',
        (
            SELECT jsonb_build_object(
                'latest_feature_date', (SELECT MAX(base_date) FROM feature_store_daily),
                'global_feature_count', (
                    SELECT COUNT(*)
                    FROM feature_store_daily
                    WHERE symbol = 'GLOBAL'
                      AND base_date = (SELECT MAX(base_date) FROM feature_store_daily)
                ),
                'key_global_features', (
                    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.feature_name)
                    FROM (
                        SELECT feature_name, feature_value
                        FROM feature_store_daily
                        WHERE symbol = 'GLOBAL'
                          AND base_date = (SELECT MAX(base_date) FROM feature_store_daily)
                          AND feature_name IN (
                              'KR_YIELD_SPREAD_10Y_3Y',
                              'KR_CREDIT_SPREAD_AA_3Y',
                              'KR_CREDIT_SPREAD_BBB_3Y',
                              'USDKRW_1D_CHG_PCT',
                              'KR10Y_1D_CHG_BP',
                              'KR10Y_20D_CHG_BP'
                          )
                    ) x
                )
            )
        ),
        'pipeline_health',
        (
            SELECT jsonb_agg(to_jsonb(x))
            FROM (
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
            ) x
        ),
        'quality_flags',
        (
            SELECT jsonb_build_object(
                'has_ecos_kr10y_today', EXISTS (
                    SELECT 1
                    FROM normalized_macro_series
                    WHERE series_id = 'KR_GOVT_10Y'
                      AND base_date = (SELECT MAX(base_date) FROM normalized_macro_series WHERE series_id = 'KR_GOVT_10Y')
                ),
                'global_macro_has_kr10y', EXISTS (
                    SELECT 1
                    FROM normalized_global_macro_daily
                    WHERE base_date = (SELECT MAX(base_date) FROM normalized_global_macro_daily)
                      AND kr10y IS NOT NULL
                ),
                'global_macro_has_market_flows', EXISTS (
                    SELECT 1
                    FROM normalized_global_macro_daily
                    WHERE base_date = (SELECT MAX(base_date) FROM normalized_global_macro_daily)
                      AND kospi_individual_net_buy IS NOT NULL
                      AND kospi_foreign_net_buy IS NOT NULL
                      AND kospi_institutional_net_buy IS NOT NULL
                      AND kosdaq_individual_net_buy IS NOT NULL
                      AND kosdaq_foreign_net_buy IS NOT NULL
                      AND kosdaq_institutional_net_buy IS NOT NULL
                ),
                'sample_rows_have_snapshot_fields', (
                    SELECT jsonb_build_object(
                        '005930_ok', EXISTS (
                            SELECT 1
                            FROM normalized_stock_prices_daily p
                            JOIN normalized_stock_supply_daily s
                              ON s.symbol = p.symbol AND s.base_date = p.base_date
                            WHERE p.symbol = '005930'
                              AND p.base_date = (SELECT MAX(base_date) FROM normalized_stock_prices_daily)
                              AND p.outstanding_shares IS NOT NULL
                              AND s.foreign_holding_ratio IS NOT NULL
                        ),
                        '035420_ok', EXISTS (
                            SELECT 1
                            FROM normalized_stock_prices_daily p
                            JOIN normalized_stock_supply_daily s
                              ON s.symbol = p.symbol AND s.base_date = p.base_date
                            WHERE p.symbol = '035420'
                              AND p.base_date = (SELECT MAX(base_date) FROM normalized_stock_prices_daily)
                              AND p.outstanding_shares IS NOT NULL
                              AND s.foreign_holding_ratio IS NOT NULL
                        )
                    )
                )
            )
        )
    )
) AS verification_report;
