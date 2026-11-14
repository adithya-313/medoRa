# ADR-003: Mutation Testing Tool

## Status
Accepted (`mutmut` selected)

## Context
Phase 1 Round 7a benchmarks mutation-testing candidates for `medoRa` on Windows-hosted Docker against fixed targets, per `docs/adr/ADR-003-criteria.md`.

## Candidates
- `mutmut` 3.8.1 (https://github.com/boxed/mutmut/blob/main/LICENSE)
- `cosmic-ray` 8.9.0 (https://github.com/sixty-north/cosmic-ray/blob/main/LICENCE.txt)
- `pytest-gremlins` 1.13.2 (https://github.com/mikelane/pytest-gremlins/blob/main/LICENSE)

## Targets
- Source: `cloud/experiments/assignment.py`, `cloud/experiments/analysis.py`, `tools/ci/test_change_gate.py`
- Tests: `tests/cloud/test_experiment_assignment.py`, `tests/cloud/test_experiment_analysis.py`, `tests/ci/test_test_change_gate.py`
- Execution: `python:3.12` container (`medora-adr003-run`), repo mounted at `/repo`, `uv sync --group dev --group adr003`.

## Hard Rules (H1-H5) and Metrics

| Candidate | H1 | H2 | H3 | H4 | H5 | M1 median (s) | M2 mutants | M3 (%) | M4 native Windows |
|---|:---:|:---:|:---:|:---:|:---:|---:|---:|---:|---|
| mutmut | PASS | PASS | PASS | PASS | PASS | 25.13 | 115 | 73.04 | no (exit 1) |
| cosmic-ray | PASS | PASS | PASS | PASS | PASS | 1659.57 | 631 | 80.67 | yes (exit 0) |
| pytest-gremlins | PASS | PASS | PASS | PASS | PASS | 316.56 | 156 | 85.90 | yes (exit 0) |

## Candidate Details
### mutmut
- H1: mutmut install/run check: mutmut, version 3.8.1
- H2: pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from collection; target unit tests under tests/ still run via pytest -m unit unchanged, selected via [tool.mutmut] pytest_add_cli_args_test_selection
- H3: mutmut export-cicd-stats emits killed/survived/timeout counts
- H4: survivors(weakened)=14 > survivors(full)=5 (full=5, weakened=14)
- M3: killed=84, survived=31, timeouts=0 (timeouts counted as killed); score = 73.04%
- M4: no exit=1 note=native Windows host attempt exited 1: To run mutmut on Windows, please use the WSL. Native windows support is tracked in issue https://github.com/boxed/mutmut/issues/397

### cosmic-ray
- H1: cosmic-ray install/run check: cosmic-ray, version 8.9.0
- H2: pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from collection; target unit tests under tests/ still run via pytest -m unit unchanged, configured via cosmic-ray test-command
- H3: cosmic-ray report emits killed/survived counts
- H4: survivors(weakened)=12 > survivors(full)=2 (full=2, weakened=12)
- M3: killed=509, survived=122, timeouts=0 (timeouts counted as killed); score = 80.67%
- M4: yes exit=0 note=native Windows host attempt exited 0: cosmic-ray, version 8.9.0

### pytest-gremlins
- H1: pytest-gremlins install/run check: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]
- H2: pytest.ini norecursedirs adds bench/adr003/demo to isolate demo tests from collection; target unit tests under tests/ still run via pytest -m unit unchanged under pytest --gremlins
- H3: pytest-gremlins report emits zapped/survived counts; no timeout concept (timeouts=0)
- H4: survivors(weakened)=8 > survivors(full)=0 (full=0, weakened=8)
- M3: killed=134, survived=22, timeouts=0 (timeouts counted as killed); score = 85.90%
- M4: yes exit=0 note=native Windows host attempt exited 0: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]

## Decision
- Winner: `mutmut`
- Rule outcome: Lowest M1 (25.13s) among H1-H5 passers
- Unit threshold: action=set score=70.0 (M3 73.04% rounded down to 70)

## Consequences
- `mutmut` selected as mutation-testing tool for medoRa unit tier.
- Unit tier minimum mutation score set to 70.0%.
- `adr003` remains an opt-in dependency group; normal `uv sync` does not install it.

## Limits
- Single-machine, single-run benchmark on Windows host with Docker Desktop; timings reflect `python:3.12` container execution and host load.
- Harness install/sync result: `install_ok=True`.
- M4 native Windows attempts were performed on the host after container run; tools not installed natively reported as unavailable with recorded exit/detail (see per-candidate M4).
- Results are from harness-written `bench/adr003/results/results.json`; no metrics were typed by hand.
- Demo tests remain excluded from normal collection via `pytest.ini` `norecursedirs`.

