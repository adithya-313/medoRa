# v1 test baseline (docker-with-db-health-risk)

**Note:** Environment installed into the container filesystem (image v1-baseline-env), warm wheel cache.

- rows (one per result): 51, distinct (file, name) pairs: 51, passed: 51, failed: 0, error: 0, skipped: 0
- tag: v1-final
- commit: 3b121e1e14ac77e8883864136e7409bce7c983aa
- run_label: docker-with-db-health-risk
- timestamp_iso: 2026-10-20T17:44:15.522703+00:00
- os_name: Linux container (image v1-baseline-env) on host Windows 11
- python_version: Python 3.11.17
- wall_seconds: 8.0
- junit_sha256: 0a7a065b8987bf4327cbc9f4907d85b3ac700aa1fdd5f9751213eb339286e6fb
- note: Environment installed into the container filesystem (image v1-baseline-env), warm wheel cache.

| file | test | outcome | seconds | reason |
|---|---|---|---|---|
| app.health_risk.tests.test_feature_flag.TestFeatureFlagAndGate | test_both_true_returns_true | passed | 0.040 |  |
| app.health_risk.tests.test_feature_flag.TestFeatureFlagAndGate | test_global_true_tenant_false_returns_false | passed | 0.002 |  |
| app.health_risk.tests.test_feature_flag.TestFeatureFlagAndGate | test_global_false_tenant_true_returns_false | passed | 0.001 |  |
| app.health_risk.tests.test_feature_flag.TestFeatureFlagAndGate | test_both_false_returns_false | passed | 0.002 |  |
| app.health_risk.tests.test_feature_flag.TestFeatureFlagAndGate | test_global_default_is_false | passed | 0.016 |  |
| app.health_risk.tests.test_ingestion_service.TestIngestionSuccess | test_all_three_diseases_are_scored_and_saved | passed | 0.024 |  |
| app.health_risk.tests.test_ingestion_service.TestIngestionSuccess | test_no_weather_row_still_scores_all_diseases | passed | 0.007 |  |
| app.health_risk.tests.test_ingestion_service.TestIngestionFailureIsolation | test_one_disease_failure_does_not_block_others | passed | 0.031 |  |
| app.health_risk.tests.test_ingestion_service.TestIngestionDBFailureIsolation | test_db_commit_failure_does_not_block_remaining_diseases | passed | 0.010 |  |
| app.health_risk.tests.test_ingestion_service.TestIngestionFeatureFlagIndependence | test_no_feature_flag_check_inside_ingestion | passed | 0.010 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_low_band | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_moderate_band | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_high_band | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_severe_band | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_low_moderate_boundary | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_moderate_high_boundary | passed | 0.001 |  |
| app.health_risk.tests.test_risk_tier.TestComputeRiskTier | test_high_severe_boundary | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoreValidation | test_raises_value_error_when_score_is_150 | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoreValidation | test_raises_value_error_when_score_is_negative_10 | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoreValidation | test_accepts_valid_score | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoringStrategyContract | test_cannot_instantiate_abstract_class_directly | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoringStrategyContract | test_transparent_formula_scorer_can_be_instantiated | passed | 0.001 |  |
| app.health_risk.tests.test_scaffold.TestRiskScoringStrategyContract | test_transparent_formula_scorer_returns_valid_risk_score | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyHigh | test_high_humidity_high_rain | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyHigh | test_boundary_high | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyHigh | test_extreme_values | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyModerate | test_high_humidity_only | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyModerate | test_high_rain_only | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyLow | test_both_low | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyLow | test_barely_below_thresholds | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyLow | test_zero_values | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyMissingData | test_none_humidity | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyMissingData | test_none_rain | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestWeatherAnomalyMissingData | test_both_none | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaRealData | test_high_trend_high_weather | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaRealData | test_mid_trend_moderate_weather | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaRealData | test_zero_trend_low_weather | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaFallbackNeutral | test_trend_none_uses_weather_only | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaFallbackNeutral | test_trend_none_moderate_weather | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaFallbackNeutral | test_trend_none_weather_partial | passed | 0.002 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaFallbackNeutral | test_all_none | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaContributingFactors | test_all_inputs_present_in_factors | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaContributingFactors | test_partial_inputs_in_factors | passed | 0.001 |  |
| app.health_risk.tests.test_scoring_formula.TestScoringFormulaContributingFactors | test_all_none_in_factors | passed | 0.002 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherSuccess | test_returns_float_when_pytrends_succeeds | passed | 0.009 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherSuccess | test_returns_float_at_boundaries | passed | 0.005 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherFailure | test_returns_none_when_pytrends_times_out | passed | 0.012 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherFailure | test_returns_none_when_pytrends_returns_empty | passed | 0.004 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherCircuitBreaker | test_returns_none_without_calling_pytrends_when_circuit_open | passed | 0.015 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherCache | test_uses_cached_value_without_calling_pytrends | passed | 0.003 |  |
| app.health_risk.tests.test_trend_fetcher.TestTrendFetcherCache | test_cache_miss_calls_pytrends | passed | 0.009 |  |
