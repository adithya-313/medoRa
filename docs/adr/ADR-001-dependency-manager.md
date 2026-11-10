# ADR-001: Python Dependency Manager Selection

**Date**: 2026-11-10  
**Status**: Accepted  
**Git Commit**: `f24784a33abe9e17f4afb925aafbad3ef8c0b3df`  
**Random Seed**: `1729`  
**Input Spec SHA-256**: `1f58ae0fff8065232ccaa3e5ca16cae45bd72ec84cb00a221e8822c33f635dad`  

## Context

medoRa requires deterministic, fast, and secure Python dependency management across local development, Docker builds, and CI pipelines. Phase 1 Round 1 benchmarks candidate tools empirically on the host Windows laptop against 12 unpinned top-level packages.

## Candidates

- `uv` (uv 0.13.7 (316c827e0 2026-11-03 x86_64-pc-windows-msvc))
- `poetry` (Poetry (version 2.5.1))
- `pip-tools` (pip-compile, version 7.6.2)
- Baseline reference: `pip` (pip 25.0.1 from C:\Users\sinne\AppData\Local\Programs\Python\Python312\Lib\site-packages\pip (python 3.12))

## Criteria

Pre-registered evaluation criteria are recorded in [ADR-001-criteria.md](ADR-001-criteria.md):
- **H1**: Reproducible lock (byte-identical lock files from scratch).
- **H2**: Package hashes for every dependency entry.
- **H3**: Zero exit code on lock and install on Windows.
- **H4**: Python runtime version pinned in project or lock.
- **Decision Rule**: Lowest median warm install among H1–H4 qualifying tools; ties within 10% broken by lowest median cold install.

## Benchmark Results

Host environment: Windows-11-10.0.26200-SP0, CPU: AMD64 Family 25 Model 124 Stepping 0, AuthenticAMD, RAM: 15.3 GB, Python: 3.12.10.

| Tool | Version | H1 | H2 | H3 | H4 | Median Lock (s) | Median Cold (s) | Median Warm (s) |
|---|---|:---:|:---:|:---:|:---:|---:|---:|---:|
| uv | uv 0.13.7 (316c827e0 2026-11-03 x86_64-pc-windows-msvc) | PASS | PASS | PASS | PASS | 1.45 | 7.44 | 1.30 |
| poetry | Poetry (version 2.5.1) | PASS | PASS | PASS | PASS | 12.71 | not measured | 7.08 |
| pip-tools | pip-compile, version 7.6.2 | PASS | PASS | FAIL | PASS | 39.80 | 48.35 | 47.19 |
| pip | pip 25.0.1 from C:\Users\sinne\AppData\Local\Programs\Python\Python312\Lib\site-packages\pip (python 3.12) | FAIL | FAIL | PASS | FAIL | N/A | 45.56 | 42.12 |

### Divergence Warnings

> [!WARNING]
> WARNING: `pip-tools` median cold install (48.35s) is within 20% of warm install (47.19s); cache may not have forced complete network re-downloads.

> [!WARNING]
> WARNING: `pip` median cold install (45.56s) is within 20% of warm install (42.12s); cache may not have forced complete network re-downloads.

### Cold Install Verification Per Repetition

| Tool | Repetition | Cold Install (s) | Cache Bytes Before | Cache Bytes After | Cold Status |
|---|:---:|---:|---:|---:|:---:|
| uv | 1 | 7.44 | 15,704,386 | 0 | ok |
| uv | 2 | 8.48 | 15,704,386 | 0 | ok |
| uv | 3 | 9.12 | 15,704,386 | 0 | ok |
| uv | 4 | 7.20 | 15,704,386 | 0 | ok |
| uv | 5 | 6.90 | 15,704,946 | 0 | ok |
| poetry | 1 | not measured | 59,087,262 | 52,396,044 | not_measured |
| poetry | 2 | not measured | 59,087,176 | 52,396,044 | not_measured |
| poetry | 3 | not measured | 59,087,163 | 52,396,044 | not_measured |
| poetry | 4 | not measured | 59,087,167 | 52,396,044 | not_measured |
| poetry | 5 | not measured | 59,087,510 | 52,396,044 | not_measured |
| pip-tools | 1 | 48.35 | 12,083 | 12,083 | ok |
| pip-tools | 2 | 55.30 | 12,083 | 12,083 | ok |
| pip-tools | 3 | 54.78 | 12,083 | 12,083 | ok |
| pip-tools | 4 | 43.08 | 12,083 | 12,083 | ok |
| pip-tools | 5 | 40.12 | 12,083 | 12,083 | ok |
| pip | 1 | 45.56 | 12,083 | 12,083 | ok |
| pip | 2 | 49.24 | 12,083 | 12,083 | ok |
| pip | 3 | 47.36 | 12,083 | 12,083 | ok |
| pip | 4 | 40.24 | 12,083 | 12,083 | ok |
| pip | 5 | 42.19 | 12,083 | 12,083 | ok |

## Decision

**Selected tool**: `uv`

uv satisfied all hard criteria H1-H4 and achieved the lowest median warm install time (1.30s).

## Consequences

- medoRa adopts `uv` as its primary Python dependency manager.
- Lockfiles will be checked into source control and verified on CI.
- Development setup and CI workflows will use `uv` commands for fast reproducible sync.

## Limits

- **Single machine**: Measurements reflect this Windows laptop (SSD, Windows 11, x86_64).
- **Network variance**: Cold installs depend on PyPI latency, mitigated by interleaved random runs.
- **Windows Defender scanning**: Background filesystem scanning adds constant overhead to file writes.
- **Poetry cache limitation**: poetry's cache-clear command leaves the artifacts cache, so its cold install wasn't measured.
- **Pip cache behavior**: pip's cache held no packages, so pip/pip-tools cold vs warm isn't comparable.
- **Subprocess log truncation**: the pip-tools rep 5 failure cause wasn't captured (tail truncated in this run).
