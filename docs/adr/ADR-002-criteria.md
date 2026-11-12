# ADR-002 Criteria: Feature Flag and Experimentation Engine Selection

## Purpose

Choose how medoRa implements per-shop feature flags and experiments across services.
To ensure objectivity, all criteria and decision rules are fixed before executing measurements.

## Candidates

1. **Unleash**: Self-hosted Unleash (OSS edition).
2. **GrowthBook**: Self-hosted GrowthBook (free self-hosted edition).
3. **Homemade**: In-repo feature flags table with deterministic hash assignment.

## Hard Rules (Disqualifying)

Any candidate that fails any of the following criteria is disqualified:

* **H1: Free Self-Hosted**: Per-shop targeting, percentage assignment, and local (in-process) evaluation are all available without a paid licence, each backed by an official documentation or licence URL.
* **H2: Offline Evaluation**: After an initial sync, with the server stopped, evaluating 10,000 shop IDs succeeds and yields 100% identical variants compared to online evaluation.
* **H3: Cross-Language Consistency**: Python and Node.js evaluations of the identical 10,000 shop IDs produce 100% identical variant assignments.
* **H4: Exposure Hook**: Application code can intercept and record every experiment exposure into internal storage, such that recorded exposures equal the exact number of evaluations.
* **H5: Self-Contained**: Runs entirely within local Docker containers with zero required external SaaS dependencies.

## Measured Metrics

Evaluated across 10,000 shop IDs (`shop-00001` through `shop-10000`) for experiment `adr002-aa` with a 50/50 split:

* **M1: Extra Production Containers**: Number of additional production service containers required to run the candidate (homemade = 0).
* **M2: Server RAM (MiB)**: Median memory usage from 5 `docker stats` samples across the candidate's containers following sync.
* **M3: Evaluation Latency (ms)**: Median wall-clock duration in milliseconds to evaluate all 10,000 shops in Python across 5 repetitions.
* **M4: Distribution Balance**: Chi-square p-value testing the 50/50 variant split (informational; p < 0.01 is flagged in Limits).
* **M5: Built-in Statistics Engine**: Whether the solution provides a built-in statistical analysis engine (yes/no; informational only).

## Decision Rule

1. Filter to candidates passing all hard rules (**H1–H5**).
2. Among passing candidates, select the candidate requiring the fewest extra production containers (**M1**).
3. **Tie-Break 1**: In the event of a tie on M1, select the candidate with lower server RAM usage (**M2**).
4. **Tie-Break 2**: In the event of a tie on M2, select the candidate with lower evaluation time (**M3**).

## Limits

A comprehensive Limits section documenting host environment details, Windows Defender impact, and unmeasured factors will be generated from benchmark results upon completion.
