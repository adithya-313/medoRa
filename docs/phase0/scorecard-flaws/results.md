# v1 vendor-scorecard flaw tests

Verdict: OK: every flaw test failed with an AssertionError and the positive control passed

| flaw | what is wrong | v1 lines | what v1 returned vs what is correct | outcome |
|---|---|---|---|---|
| F1 | no orders -> TRUSTED with rate 100 | 30-39 | v1 returned risk_level=TRUSTED, success_rate=100 for a supplier with zero orders; correct: an unrated supplier is not TRUSTED | failed |
| F2 | score = 100 - 15 x failures, not a rate | 73 | v1 scored 2-of-2 failing = 70 and 7-of-200 failing = 0; correct: failing every order scores lower than failing 3.5% of orders | failed |
| F3 | pending orders not yet due count as failures | 55-59 | v1 returned recent_failures=1 with 5 good orders and 1 PENDING order due on 2026-03-10; correct: 0 | failed |
| F4 | no tenant filter: every shop's orders are mixed | 26-28 | v1 returned recent_failures=3 to viewer shop-A, who has only good orders; the failures belong to shop-B; correct: 0 | failed |
| F5 | money computed as float, not exact Decimal | 44, 62 | v1 returned 100000099989999.98 as float; correct: exact Decimal 100000099989999.99 | failed |
| F6 | failures never age: no time window | 45-65 | v1 scored the 3-year-old failure 85 and the 7-day-old failure 85; correct: the old failure scores strictly higher | failed |
| F7 | negative loss when cost_price > expected_mrp | 61-62 | v1 returned financial_loss=-100.0; correct: >= 0 | failed |
| F8 | success rate truncated by int() | 70 | v1 returned delivery_success_rate=99 for 199 good orders of 200 (99.5 percent, truncated by int()); correct: not 99 | failed |

Positive control (test_positive_control): passed


## Run meta

- tag: v1-final
- commit: 3b121e1e14ac77e8883864136e7409bce7c983aa
- image_id: sha256:f1fa9c405975b2d8152623bf7c69dacd6c487c5cd5edebea5c67e3ad2bfcfd2d
- timestamp: 2026-10-20T18:22:47.551778+00:00
- machine: Windows-11-10.0.26200-SP0
- junit_sha256: c53216ab14a8bb5e16b678696d25934e83a14ed8cbd76052788e378e84fb7098
