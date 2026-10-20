# v1 test baseline (docker-no-services)

**Note:** Install ran on a warm cache: pip 3394.5 s, downloaded 0.0 MB, served from cache 2984.2 MB (sizes are an ESTIMATE from pip log lines; not a cold-install figure unless labelled cold).

- total: 232, passed: 197, failed: 27, error: 5, skipped: 3
- tag: v1-final
- commit: 3b121e1e14ac77e8883864136e7409bce7c983aa
- run_label: docker-no-services
- timestamp_iso: 2026-10-20T00:43:42.539452+00:00
- os_name: Linux container (image medora-v1-baseline:py311) on host Windows 11
- python_version: Python 3.11.17
- wall_seconds: 260.9
- junit_sha256: b23a94ba8242215e268179a66dbf7334ce141ec4be0da76c18f487c324bb53c5
- note: Install ran on a warm cache: pip 3394.5 s, downloaded 0.0 MB, served from cache 2984.2 MB (sizes are an ESTIMATE from pip log lines; not a cold-install figure unless labelled cold).

| file | test | outcome | seconds | reason |
|---|---|---|---|---|
| integration.test_auto_po_tenant.TestAutoPOTenantAssignment | test_auto_po_creates_with_correct_tenant_id | passed | 0.076 |  |
| integration.test_auto_po_tenant.TestAutoPOTenantAssignment | test_auto_po_fails_cleanly_without_tenant | passed | 0.022 |  |
| integration.test_checkout_idempotency.TestCheckoutIdempotencyGuard | test_duplicate_idempotency_hash_returns_409 | passed | 0.024 |  |
| integration.test_checkout_idempotency.TestCheckoutFefoLockGuard | test_fefo_lock_expired_returns_409 | failed | 0.021 | KeyError: 'verify_inventory_lock_exists' |
| integration.test_health_risk_job_resilience.TestHealthRiskJobResilience | test_job_survives_sustained_pytrends_failure | error | 0.014 | failed on setup with "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_success_path_all_three_diseases_scored | failed | 0.009 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_success_path_all_three_diseases_scored | error | 0.001 | failed on teardown with "sqlalchemy.exc.PendingRollbackError: This Session's transaction has been rolled back due to a previous exception during flush. To begin a new transaction with this Session, first issue Session.rollback(). Original exception was: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_no_weather_no_trend_uses_fallback_neutral | failed | 0.007 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_no_weather_no_trend_uses_fallback_neutral | error | 0.002 | failed on teardown with "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_real_open_meteo_weather_used_in_scoring | failed | 1.058 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_real_open_meteo_weather_used_in_scoring | error | 0.002 | failed on teardown with "sqlalchemy.exc.PendingRollbackError: This Session's transaction has been rolled back due to a previous exception during flush. To begin a new transaction with this Session, first issue Session.rollback(). Original exception was: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_trend_fetch_exception_skips_that_disease | failed | 0.011 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_health_risk_pipeline.TestHealthRiskRealPipeline | test_trend_fetch_exception_skips_that_disease | error | 0.002 | failed on teardown with "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| integration.test_overdue_po_notifications | test_escalation_and_duplicate_prevention | passed | 0.205 |  |
| integration.test_overdue_po_notifications | test_tenant_isolation | passed | 0.039 |  |
| integration.test_procurement_vendor_scoring.TestProcurementVendorScoring | test_vendor_scoring_picks_supplier_with_most_fulfilled_orders | failed | 0.042 | assert 409 == 200 |
| integration.test_purchase_order_chronological_sort | test_purchase_orders_sort_chronologically | passed | 0.044 |  |
| integration.test_purchase_order_chronological_sort | test_purchase_order_endpoint_auto_populates_created_at | failed | 0.066 | assert 400 == 200 |
| integration.test_purchase_order_ledger.TestPurchaseOrderLedger | test_ledger_shows_vendor_name_and_handles_missing_supplier | passed | 0.062 |  |
| integration.test_purchase_order_ledger_values.TestLedgerValueCalculation | test_ledger_values_are_correct_for_all_statuses | passed | 0.029 |  |
| integration.test_purchase_order_receive_logic | test_single_full_delivery | failed | 0.039 | assert 500 == 200 |
| integration.test_purchase_order_receive_logic | test_two_partial_deliveries | failed | 0.038 | assert 500 == 200 |
| integration.test_purchase_order_receive_logic | test_over_delivery | failed | 0.037 | assert 500 == 200 |
| integration.test_purchase_order_receive_logic | test_auto_po_idempotency_with_submitted | passed | 0.044 |  |
| integration.test_purchase_orders_tenant_isolation.TestPurchaseOrderTenantIsolation | test_tenant_can_only_see_own_orders | passed | 0.029 |  |
| integration.test_purchase_orders_tenant_isolation.TestPurchaseOrderTenantIsolation | test_unauthenticated_request_handled_correctly | passed | 0.026 |  |
| test_audit_log_endpoint | test_audit_logs_tenant_isolation | passed | 0.076 |  |
| test_audit_log_endpoint | test_audit_logs_pagination | passed | 0.089 |  |
| test_audit_log_endpoint | test_audit_logs_filters | passed | 0.056 |  |
| test_audit_logging.TestAuditLoggingOnReceive | test_receive_writes_audit_with_old_and_new_values | passed | 0.010 |  |
| test_audit_logging.TestAuditLoggingOnReceive | test_receive_does_not_commit_separately_for_audit | passed | 0.013 |  |
| test_audit_logging.TestAuditLoggingOnCreate | test_create_writes_audit_with_new_value | failed | 0.009 | fastapi.exceptions.HTTPException: 400: Expected delivery date cannot be in the past. |
| test_backend_resilience | test_vector_a_bad_token | failed | 0.022 | assert 404 in [401, 403] |
| test_backend_resilience | test_vector_b_sql_injection | failed | 0.023 | assert 404 == 200 |
| test_backend_resilience | test_vector_c_missing_supplier | failed | 0.032 | assert 404 == 200 |
| test_backend_resilience | test_vector_d_payload_fuzzing | failed | 0.042 | assert 404 == 422 |
| test_checkout_and_vendor_audit | test_checkout_new_patient_writes_4_audit_entries | passed | 0.068 |  |
| test_checkout_and_vendor_audit | test_checkout_existing_patient_writes_3_audit_entries | passed | 0.081 |  |
| test_checkout_and_vendor_audit | test_add_vendor_writes_1_audit_entry | passed | 0.068 |  |
| test_drug_resolver.TestDrugResolver | test_exact_match | passed | 0.016 |  |
| test_drug_resolver.TestDrugResolver | test_fuzzy_match_single | passed | 0.024 |  |
| test_drug_resolver.TestDrugResolver | test_disambiguate_multiple_matches | passed | 0.024 |  |
| test_drug_resolver.TestDrugResolver | test_no_match_gibberish | passed | 0.015 |  |
| test_drug_resolver.TestDrugResolver | test_forced_sku_short_circuits | passed | 0.005 |  |
| test_drug_resolver.TestDrugResolver | test_unforced_sku_retains_behavior | passed | 0.013 |  |
| test_drug_resolver.TestDrugResolver | test_exact_match_per_token_single | passed | 0.013 |  |
| test_drug_resolver.TestDrugResolver | test_exact_match_per_token_none | passed | 0.012 |  |
| test_drug_resolver.TestDrugResolver | test_exact_match_per_token_multiple | passed | 0.016 |  |
| test_drug_resolver.TestDrugResolver | test_fuzzy_tie_break_generic_wins | passed | 0.018 |  |
| test_drug_resolver.TestDrugResolver | test_fuzzy_tie_break_two_brands_ambiguous | passed | 0.013 |  |
| test_drug_resolver.TestDrugResolver | test_fuzzy_tie_break_three_way_ambiguous | passed | 0.018 |  |
| test_drug_resolver.TestDrugResolver | test_pipeline_disambiguate_short_circuit | passed | 0.019 |  |
| test_drug_resolver.TestDrugResolver | test_pipeline_no_match_short_circuit | passed | 0.017 |  |
| test_drug_resolver.TestDrugResolver | test_pipeline_exact_regression | passed | 0.016 |  |
| test_drug_resolver.TestDrugResolver | test_brand_match_exact | passed | 0.019 |  |
| test_drug_resolver.TestDrugResolver | test_brand_match_fuzzy | passed | 0.013 |  |
| test_drug_resolver.TestDrugResolver | test_rca_case_2_pn40 | passed | 0.012 |  |
| test_drug_resolver.TestDrugResolver | test_rca_case_4_punctuation | passed | 0.025 |  |
| test_drug_resolver.TestDrugResolver | test_hyphenated_vs_bare_mismatch | passed | 0.017 |  |
| test_drug_resolver.TestRCARegression | test_rca_funnel_queries | passed | 0.085 |  |
| test_drug_resolver.TestRCARegression | test_no_drug_no_db_call | passed | 0.018 |  |
| test_drug_resolver.TestDrugResolverRealDB | test_real_db_fuzzy_match_sql_syntax | skipped | 0.013 | Real database not available for this test. |
| test_drug_resolver.TestDrugResolverRealDB | test_real_db_spot_check_queries_resolve | skipped | 0.005 | Real database not available for this test. |
| test_filter_fix | test_data_quality_present | passed | 0.011 |  |
| test_filter_fix | test_data_quality_missing_fallback_triggered | passed | 0.008 |  |
| test_filter_fix | test_edge_case_survives | passed | 0.013 |  |
| test_filter_fix | test_above_threshold_full_data | passed | 0.007 |  |
| test_h3_context_dedup | test_dedup_identical_records | failed | 0.007 | Failed: async def functions are not natively supported. |
| test_h3_context_dedup | test_dedup_different_skus | failed | 0.006 | Failed: async def functions are not natively supported. |
| test_h3_context_dedup | test_dedup_completeness_preference | failed | 0.015 | Failed: async def functions are not natively supported. |
| test_h3_rag_guard | test_rag_guard_multiple_distinct_skus | failed | 0.006 | Failed: async def functions are not natively supported. |
| test_h3_rag_guard | test_rag_guard_single_distinct_sku | failed | 0.007 | Failed: async def functions are not natively supported. |
| test_health_risk_router.TestHealthRiskCurrentEndpoint | test_tenant_with_two_disease_scores_returns_both | passed | 0.031 |  |
| test_health_risk_router.TestHealthRiskCurrentEndpoint | test_tenant_with_zero_scores_returns_empty_list | passed | 0.022 |  |
| test_health_risk_router.TestHealthRiskCurrentEndpoint | test_tenant_with_no_pincode_returns_404 | passed | 0.019 |  |
| test_health_risk_router.TestHealthRiskCurrentEndpoint | test_tenant_not_found_returns_404 | passed | 0.019 |  |
| test_health_risk_router.TestHealthRiskCurrentEndpoint | test_missing_x_tenant_id_header_returns_403 | passed | 0.018 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_majority_total_fallback_returns_degraded | passed | 0.024 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_weather_only_fallback_does_not_count_as_degraded | passed | 0.022 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_majority_real_data_returns_real_data | passed | 0.025 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_single_total_fallback_run_is_degraded | passed | 0.022 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_mixed_diseases_get_independent_status | passed | 0.024 |  |
| test_health_risk_router.TestHealthRiskCurrentDataStatus | test_zero_scores_still_returns_empty_list | passed | 0.023 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_tenant_with_five_score_rows_across_different_days_returns_all_five_ordered_oldest_first | passed | 0.024 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_tenant_with_zero_score_rows_returns_empty_list | passed | 0.021 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_days_equals_zero_returns_400 | passed | 0.020 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_days_negative_returns_400 | passed | 0.018 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_days_exceeds_maximum_returns_400 | passed | 0.018 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_days_omitted_defaults_to_30 | passed | 0.022 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_tenant_with_no_pincode_returns_404 | passed | 0.020 |  |
| test_health_risk_router.TestHealthRiskHistoryEndpoint | test_scores_do_not_include_data_status | passed | 0.021 |  |
| test_health_risk_router.TestHealthRiskExplainEndpoint | test_score_belongs_to_calling_tenant_returns_full_breakdown | passed | 0.021 |  |
| test_health_risk_router.TestHealthRiskExplainEndpoint | test_score_id_does_not_exist_returns_404 | passed | 0.327 |  |
| test_health_risk_router.TestHealthRiskExplainEndpoint | test_score_exists_but_belongs_to_different_pincode_returns_404 | passed | 0.020 |  |
| test_health_risk_router.TestHealthRiskExplainEndpoint | test_calling_tenant_has_no_pincode_returns_404 | passed | 0.021 |  |
| test_health_risk_router.TestHealthRiskExplainEndpoint | test_missing_x_tenant_id_header_returns_403 | passed | 0.019 |  |
| test_health_risk_tasks.TestHealthRiskIngestionTask | test_global_flag_disabled_skips_everything | passed | 0.025 |  |
| test_health_risk_tasks.TestHealthRiskIngestionTask | test_real_success_already_enabled | passed | 0.008 |  |
| test_health_risk_tasks.TestHealthRiskIngestionTask | test_empty_result_counts_as_failure | passed | 0.008 |  |
| test_health_risk_tasks.TestHealthRiskIngestionTask | test_exception_does_not_stop_subsequent_tenants | passed | 0.013 |  |
| test_health_risk_tasks.TestHealthRiskIngestionTask | test_two_tenants_summary_mixed_results | passed | 0.011 |  |
| test_inference_edge_cases | test_a_happy_path | failed | 0.026 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| test_inference_edge_cases | test_b_missing_weights_file | failed | 0.020 | sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "localhost" (::1), port 5432 failed: Connection refused |
| test_inference_edge_cases | test_c_vram_oom_simulation | passed | 0.019 |  |
| test_inference_edge_cases | test_d_bad_payload | failed | 0.023 | assert 404 == 422 |
| test_intent_gate.TestClassifyIntent | test_empty_query_no_sku_returns_no_intent | passed | 0.005 |  |
| test_intent_gate.TestClassifyIntent | test_drug_without_keyword_returns_ambiguous | passed | 0.005 |  |
| test_intent_gate.TestClassifyIntent | test_objective_keyword_without_sku_returns_no_intent | passed | 0.004 |  |
| test_intent_gate.TestClassifyIntent | test_drug_with_objective_keyword_returns_inventory | passed | 0.004 |  |
| test_intent_gate.TestClassifyIntent | test_neither_drug_nor_keyword_returns_no_intent | passed | 0.004 |  |
| test_intent_gate.TestClassifyIntent | test_garbage_input_does_not_raise | passed | 0.006 |  |
| test_intent_gate.TestClassifyIntent | test_sql_injection_shaped_input_treated_as_plain_text | passed | 0.004 |  |
| test_intent_gate.TestNoIntentShortCircuit | test_no_intent_query_never_calls_downstream_services | passed | 0.018 |  |
| test_phase1_isolation.TestTenantIsolationStructuralValidation | test_medication_batches_requires_tenant_id | passed | 0.005 |  |
| test_phase1_isolation.TestTenantIsolationStructuralValidation | test_sale_receipts_requires_tenant_id | passed | 0.004 |  |
| test_phase1_isolation.TestTenantIsolationStructuralValidation | test_sale_line_items_requires_tenant_id | passed | 0.005 |  |
| test_phase1_isolation.TestTenantIsolationStructuralValidation | test_purchase_orders_requires_tenant_id | passed | 0.005 |  |
| test_phase1_isolation.TestClinicalMasterIndexFields | test_pregnancy_category_column_exists | passed | 0.005 |  |
| test_phase1_isolation.TestClinicalMasterIndexFields | test_pediatric_age_threshold_column_exists | passed | 0.005 |  |
| test_phase1_isolation.TestClinicalMasterIndexFields | test_interaction_matrix_column_exists | passed | 0.004 |  |
| test_phase1_isolation.TestClinicalMasterIndexFields | test_is_approved_by_curator_has_server_default_false | passed | 0.006 |  |
| test_phase1_isolation.TestRegulatoryDiffLogStructure | test_regulatory_diff_log_has_required_columns | passed | 0.005 |  |
| test_phase1_isolation.TestRegulatoryDiffLogStructure | test_regulatory_diff_log_curator_id_is_nullable | passed | 0.004 |  |
| test_phase1_isolation.TestScraperResilience | test_staleness_alert_raised_on_empty_feed | passed | 0.020 |  |
| test_phase1_isolation.TestScraperResilience | test_staleness_alert_metric_log_format | passed | 0.005 |  |
| test_phase1_isolation.TestScraperResilience | test_scraper_handles_database_offline_gracefully | passed | 0.005 |  |
| test_phase2_routing.TestCleanDrugNames | test_corrects_single_misspelling | passed | 0.005 |  |
| test_phase2_routing.TestCleanDrugNames | test_no_change_for_correct_spelling | passed | 0.006 |  |
| test_phase2_routing.TestCleanDrugNames | test_short_words_ignored | passed | 0.006 |  |
| test_phase2_routing.TestCleanDrugNames | test_empty_valid_names_list | passed | 0.005 |  |
| test_phase2_routing.TestExtractIntents | test_detects_pregnancy_intent | passed | 0.006 |  |
| test_phase2_routing.TestExtractIntents | test_detects_pediatric_intent | passed | 0.005 |  |
| test_phase2_routing.TestExtractIntents | test_detects_substitute_intent | passed | 0.005 |  |
| test_phase2_routing.TestExtractIntents | test_detects_objective_intent | passed | 0.006 |  |
| test_phase2_routing.TestExtractIntents | test_multiple_intents_can_coexist | passed | 0.005 |  |
| test_phase2_routing.TestExtractIntents | test_no_intents_for_irrelevant_query | passed | 0.004 |  |
| test_phase2_routing.TestExtractSku | test_extracts_hyphenated_sku | passed | 0.004 |  |
| test_phase2_routing.TestExtractSku | test_extracts_no_hyphen_sku | passed | 0.005 |  |
| test_phase2_routing.TestExtractSku | test_returns_none_when_no_sku_found | passed | 0.005 |  |
| test_phase2_routing.TestExtractSku | test_case_insensitive_matching | passed | 0.006 |  |
| test_phase2_routing.TestExtractSku | test_sku_at_end_of_query | passed | 0.005 |  |
| test_phase2_routing.TestSanitizerPipeline | test_full_pipeline_objective_misspelling | passed | 0.005 |  |
| test_phase2_routing.TestSanitizerPipeline | test_full_pipeline_pregnancy | passed | 0.005 |  |
| test_phase2_routing.TestSanitizerPipeline | test_substitute_routing_triggers | passed | 0.005 |  |
| test_phase2_routing.TestAskEndpointWithSanitizer | test_ask_endpoint_imports_and_responds | passed | 0.033 |  |
| test_phase2_routing.TestAskEndpointWithSanitizer | test_ask_endpoint_with_substitute_intent | passed | 0.031 |  |
| test_phase2_routing.TestSanitizerStatelessness | test_multiple_calls_produce_same_result | passed | 0.006 |  |
| test_phase2_routing.TestSanitizerStatelessness | test_consecutive_calls_dont_interfere | passed | 0.004 |  |
| test_phase3_safety.TestPregnancySafety | test_blocks_category_x_drug | passed | 0.006 |  |
| test_phase3_safety.TestPregnancySafety | test_blocks_category_d_drug | passed | 0.005 |  |
| test_phase3_safety.TestPregnancySafety | test_allows_category_b_drug | passed | 0.005 |  |
| test_phase3_safety.TestPregnancySafety | test_allows_when_no_pregnancy_intent | passed | 0.006 |  |
| test_phase3_safety.TestPregnancySafety | test_allows_pregnancy_when_category_is_none | passed | 0.006 |  |
| test_phase3_safety.TestPediatricSafety | test_blocks_age_restricted_drug | passed | 0.004 |  |
| test_phase3_safety.TestPediatricSafety | test_allows_age_restricted_when_patient_is_old_enough | passed | 0.006 |  |
| test_phase3_safety.TestPediatricSafety | test_allows_when_no_age_threshold | passed | 0.005 |  |
| test_phase3_safety.TestPediatricSafety | test_allows_when_no_pediatric_intent | passed | 0.004 |  |
| test_phase3_safety.TestMultipleRecords | test_blocks_if_any_record_triggers_pregnancy | passed | 0.004 |  |
| test_phase3_safety.TestMultipleRecords | test_allows_if_all_records_are_safe | passed | 0.006 |  |
| test_phase3_safety.TestClinicalNarrativeSchema | test_valid_safe_narrative | passed | 0.004 |  |
| test_phase3_safety.TestClinicalNarrativeSchema | test_valid_unsafe_narrative_with_lock_reason | passed | 0.005 |  |
| test_phase3_safety.TestClinicalNarrativeSchema | test_rejects_missing_required_field | passed | 0.006 |  |
| test_phase3_safety.TestClinicalNarrativeSchema | test_rejects_wrong_type_for_boolean | passed | 0.004 |  |
| test_phase3_safety.TestJudgeStatelessness | test_multiple_calls_produce_same_result | passed | 0.006 |  |
| test_phase3_safety.TestJudgeStatelessness | test_consecutive_calls_dont_interfere | passed | 0.006 |  |
| test_phase4_resilience.TestResultCache | test_cache_hit_returns_cached_response[asyncio] | passed | 0.019 |  |
| test_phase4_resilience.TestResultCache | test_cache_miss_proceeds_to_gemini[asyncio] | passed | 0.031 |  |
| test_phase4_resilience.TestResultCache | test_cache_store_on_success[asyncio] | passed | 0.023 |  |
| test_phase4_resilience.TestResultCache | test_cache_redis_down_still_works[asyncio] | passed | 0.021 |  |
| test_phase4_resilience.TestCircuitBreaker | test_circuit_open_uses_degraded_mode[asyncio] | passed | 0.019 |  |
| test_phase4_resilience.TestCircuitBreaker | test_circuit_half_open_recovery_success[asyncio] | passed | 0.036 |  |
| test_phase4_resilience.TestTimeoutFallback | test_timeout_returns_degraded_with_system_fallback[asyncio] | passed | 0.020 |  |
| test_phase4_resilience.TestTimeoutFallback | test_timeout_does_not_crash[asyncio] | passed | 0.021 |  |
| test_phase4_resilience.TestDegradedMode | test_degraded_from_safe_verdict[asyncio] | passed | 0.021 |  |
| test_phase4_resilience.TestDegradedMode | test_degraded_from_unsafe_verdict[asyncio] | passed | 0.016 |  |
| test_phase4_resilience.TestVersionInvalidation | test_different_versions_produce_different_cache_keys[asyncio] | passed | 0.040 |  |
| test_phase4_resilience.TestVersionInvalidation | test_version_change_misses_cache[asyncio] | passed | 0.027 |  |
| test_phase4_resilience.TestResultCache | test_cache_hit_returns_cached_response[trio] | passed | 0.021 |  |
| test_phase4_resilience.TestResultCache | test_cache_miss_proceeds_to_gemini[trio] | failed | 0.022 | AssertionError: assert ['SYSTEM_FALLBACK'] == ['Mocked Citation'] |
| test_phase4_resilience.TestResultCache | test_cache_store_on_success[trio] | failed | 0.020 | AssertionError: Expected 'set' to have been called once. Called 0 times. |
| test_phase4_resilience.TestResultCache | test_cache_redis_down_still_works[trio] | passed | 0.019 |  |
| test_phase4_resilience.TestCircuitBreaker | test_circuit_open_uses_degraded_mode[trio] | passed | 0.023 |  |
| test_phase4_resilience.TestCircuitBreaker | test_circuit_half_open_recovery_success[trio] | passed | 0.019 |  |
| test_phase4_resilience.TestTimeoutFallback | test_timeout_returns_degraded_with_system_fallback[trio] | passed | 0.019 |  |
| test_phase4_resilience.TestTimeoutFallback | test_timeout_does_not_crash[trio] | passed | 0.020 |  |
| test_phase4_resilience.TestDegradedMode | test_degraded_from_safe_verdict[trio] | passed | 0.019 |  |
| test_phase4_resilience.TestDegradedMode | test_degraded_from_unsafe_verdict[trio] | passed | 0.017 |  |
| test_phase4_resilience.TestVersionInvalidation | test_different_versions_produce_different_cache_keys[trio] | passed | 0.033 |  |
| test_phase4_resilience.TestVersionInvalidation | test_version_change_misses_cache[trio] | passed | 0.021 |  |
| test_phase4_resilience.TestSynchronousCacheVersion | test_sync_function_imports_and_returns_string | passed | 0.007 |  |
| test_phase4_resilience.TestSynchronousCacheVersion | test_sync_function_handles_redis_unavailable | passed | 0.008 |  |
| test_phase5_etl.TestTransformRowHappyPath | test_returns_sku_id | passed | 0.006 |  |
| test_phase5_etl.TestTransformRowHappyPath | test_apis_contains_generic_name | passed | 0.004 |  |
| test_phase5_etl.TestTransformRowHappyPath | test_supplies_safety_defaults | passed | 0.005 |  |
| test_phase5_etl.TestTransformRowHappyPath | test_preserves_clinical_text_fields | passed | 0.004 |  |
| test_phase5_etl.TestTransformRowResilience | test_handles_empty_dictionary | passed | 0.004 |  |
| test_phase5_etl.TestTransformRowResilience | test_handles_missing_sku_id | passed | 0.005 |  |
| test_phase5_etl.TestTransformRowResilience | test_handles_missing_indications | passed | 0.004 |  |
| test_phase5_etl.TestTransformRowResilience | test_handles_malformed_pediatric_age | passed | 0.003 |  |
| test_phase5_etl.TestTransformRowResilience | test_extracts_pregnancy_category_when_provided | passed | 0.005 |  |
| test_phase5_etl.TestCreateSemanticChunk | test_returns_string | passed | 0.004 |  |
| test_phase5_etl.TestCreateSemanticChunk | test_contains_sku_and_brand | passed | 0.005 |  |
| test_phase5_etl.TestCreateSemanticChunk | test_semicolons_replaced_with_commas | passed | 0.006 |  |
| test_phase5_etl.TestCreateSemanticChunk | test_handles_minimal_data | passed | 0.005 |  |
| test_phase5_etl.TestCreateSemanticChunk | test_includes_price_when_present | passed | 0.004 |  |
| test_pos_checkout.TestPosCheckoutSuccess | test_successful_checkout_returns_200_and_receipt_id | passed | 0.026 |  |
| test_pos_checkout.TestPosCheckoutIdempotency | test_duplicate_idempotency_key_returns_409 | passed | 0.025 |  |
| test_pos_history_tenant_isolation | test_pos_history_tenant_isolation | passed | 0.053 |  |
| test_procurement_router_health_risk.TestProcurementHealthRiskFlag | test_flag_disabled_returns_empty_list | passed | 0.021 |  |
| test_procurement_router_health_risk.TestProcurementHealthRiskFlag | test_flag_enabled_uses_health_risk_scores | passed | 0.029 |  |
| test_procurement_router_health_risk.TestProcurementHealthRiskFlag | test_no_scores_yet_returns_empty_gracefully | passed | 0.030 |  |
| test_procurement_router_health_risk.TestProcurementHealthRiskFlag | test_flag_enabled_no_high_risk_scores_returns_empty | passed | 0.027 |  |
| test_procurement_router_health_risk.TestProcurementHealthRiskFlag | test_flag_enabled_multiple_diseases_cross_threshold | passed | 0.045 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_mrp_query_returns_database_answer[asyncio] | passed | 0.011 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_stock_query_returns_database_answer[asyncio] | passed | 0.009 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_query_without_sku_id_falls_to_gemini[asyncio] | passed | 0.012 |  |
| test_rag_routing.TestAdaptiveRoutingSubjective | test_clinical_query_calls_gemini_rag[asyncio] | passed | 0.008 |  |
| test_rag_routing.TestAdaptiveRoutingSubjective | test_clinical_query_no_sku_id_still_calls_gemini[asyncio] | passed | 0.012 |  |
| test_rag_routing.TestClinicalNarrationStockInjection | test_clinical_response_includes_stock_when_available[asyncio] | passed | 3.591 |  |
| test_rag_routing.TestClinicalNarrationStockInjection | test_clinical_response_handles_missing_stock_gracefully[asyncio] | passed | 0.017 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_mrp_query_returns_database_answer[trio] | passed | 0.008 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_stock_query_returns_database_answer[trio] | passed | 0.010 |  |
| test_rag_routing.TestAdaptiveRoutingObjective | test_objective_query_without_sku_id_falls_to_gemini[trio] | passed | 0.009 |  |
| test_rag_routing.TestAdaptiveRoutingSubjective | test_clinical_query_calls_gemini_rag[trio] | passed | 0.008 |  |
| test_rag_routing.TestAdaptiveRoutingSubjective | test_clinical_query_no_sku_id_still_calls_gemini[trio] | passed | 0.008 |  |
| test_rag_routing.TestClinicalNarrationStockInjection | test_clinical_response_includes_stock_when_available[trio] | failed | 0.012 | AssertionError: Expected 'generate_content' to have been called once. Called 0 times. |
| test_rag_routing.TestClinicalNarrationStockInjection | test_clinical_response_handles_missing_stock_gracefully[trio] | failed | 0.011 | AssertionError: Expected 'generate_content' to have been called once. Called 0 times. |
| test_schema_contract | test_schema_contract_raw_sql_columns | skipped | 0.006 | Real database not available for schema contract test (Connection refused). |
| test_supplier_api | test_empty_suppliers_list | passed | 0.017 |  |
| test_supplier_api | test_add_and_list_supplier | passed | 0.020 |  |
| test_supplier_api | test_invalid_pagination_params | passed | 0.019 |  |
| test_supplier_history_api | test_supplier_history_endpoint | passed | 0.059 |  |
