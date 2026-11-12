# ADR-002: Feature Flag and Experimentation System

## Status
Accepted

## Context
medoRa requires per-shop feature flags and A/B experimentation capabilities across Python
and TypeScript/Node services. Prior to selecting an architecture, criteria and limits were
pinned in `docs/adr/ADR-002-criteria.md` and benchmarked locally across three candidates:
1. **Self-hosted Unleash (OSS edition)**: `unleashorg/unleash-server:latest` backed by PostgreSQL.
2. **Self-hosted GrowthBook (free self-hosted edition)**: `growthbook/growthbook:latest` backed by MongoDB.
3. **Homemade candidate**: In-repo flags table with deterministic SHA-256 hash assignment.

## Hard Rules Evaluation (H1-H5)

| Candidate | H1 Free Self-Hosted | H2 Offline | H3 Cross-Language | H4 Exposure Hook | H5 Self-Contained | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **homemade** | PASS | PASS | PASS | PASS | PASS | **ELIGIBLE** |
| **unleash** | PASS | PASS | PASS | FAIL | PASS | **DISQUALIFIED** |
| **growthbook** | PASS | PASS | PASS | PASS | PASS | **ELIGIBLE** |

## Measured Metrics (M1-M5)

| Candidate | M1 Containers | M2 Server RAM (MiB) | M3 Latency (10k ops, ms) | M4 Balance (p-value) | M5 Stats Engine |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **homemade** | 0 | 0.00 | 8.03 | 1.0 | No |
| **unleash** | 2 | 155.08 | 1058.97 | 0.872881 | No |
| **growthbook** | 2 | 1920.17 | 226.36 | 0.0035 | Yes |

## Decision
**Selected winner: `homemade`**.

Under the decision rule established before measurement:
- Among candidates passing all hard rules (H1–H5), select the candidate requiring the fewest extra
  production containers (M1).
- `homemade` requires **0** extra production containers, whereas `unleash` and `growthbook` both
  require **2** containers (application server + backing database).

## Consequences
### Positive
- **Zero Operational Overhead**: Eliminates operational burden of provisioning, monitoring, and scaling
  standalone feature flag servers and databases.
- **Deterministic Cross-Language Consistency**: SHA-256 hash slicing provides identical variant bucketing
  in Python and Node with sub-millisecond local execution.
- **100% Offline Resilience**: Evaluations are pure function calls that succeed without network dependencies.

### Negative / Trade-offs
- **No Built-in Experiment Statistics Engine**: Bayesian and frequentist significance testing must be
  handled by analytic pipelines rather than an embedded server UI.
- **Configuration Distribution**: Flag toggles and split percentages are defined in code or configuration
  rather than an interactive web UI.

## Limits
- **Single Machine Environment**: All measurements were executed on a single Windows laptop running
  Docker Desktop (WSL2 backend). Windows Defender and background filesystem scanning can cause slight
  evaluation latency jitter.
- **Zero Network Contention**: Tests evaluated in-process evaluation performance without network latency
  or saturation on remote endpoints.
- **Offline Cache Scope**: offline was tested with the SDK process still alive (in-memory cache);
  offline evaluation after an app restart (on-disk cache) was not tested.
- **M4 Balance Flag (p < 0.01)**: The following candidate(s) showed p < 0.01 on the 50/50 balance test: **growthbook** (p = 0.0035).
