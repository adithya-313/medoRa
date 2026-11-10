# ADR-001: Python Dependency Manager Selection

**Date**: 2026-11-10  
**Status**: Accepted  
**Git Commit**: `a62070374e94649e30211b7e3feba9bb24a81f99`  
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
| uv | uv 0.13.7 (316c827e0 2026-11-03 x86_64-pc-windows-msvc) | PASS | PASS | PASS | PASS | 0.11 | 0.50 | 0.23 |
| poetry | Poetry (version 2.5.1) | PASS | PASS | PASS | PASS | 6.27 | 6.46 | 4.52 |
| pip-tools | pip-compile, version 7.6.2 | PASS | PASS | PASS | PASS | 8.21 | 40.69 | 38.55 |
| pip | pip 25.0.1 from C:\Users\sinne\AppData\Local\Programs\Python\Python312\Lib\site-packages\pip (python 3.12) | FAIL | FAIL | PASS | FAIL | N/A | 40.76 | 37.92 |

## Decision

**Selected tool**: `uv`

uv satisfied all hard criteria H1-H4 and achieved the lowest median warm install time (0.23s).

## Consequences

- medoRa adopts `uv` as its primary Python dependency manager.
- Lockfiles will be checked into source control and verified on CI.
- Development setup and CI workflows will use `uv` commands for fast reproducible sync.

## Limits

- **Single machine**: Measurements reflect this Windows laptop (SSD, Windows 11, x86_64).
- **Network variance**: Cold installs depend on PyPI latency, mitigated by interleaved random runs.
- **Windows Defender scanning**: Background filesystem scanning adds constant overhead to file writes.
