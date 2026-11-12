# ADR-003: Mutation Testing Tool

## Status
Accepted (no eligible winner)

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
| mutmut | PASS | PASS | FAIL | FAIL | PASS | 35.37 | n/a | n/a | no (exit None) |
| cosmic-ray | PASS | PASS | FAIL | FAIL | PASS | 7.71 | n/a | n/a | no (exit None) |
| pytest-gremlins | PASS | PASS | FAIL | FAIL | PASS | 1800.66 | n/a | n/a | yes (exit 0) |

## Candidate Details
### mutmut
- H1: mutmut install/run check: mutmut, version 3.8.1
- H2: mutmut runs pytest -m unit via [tool.mutmut] config; pytest.ini untouched
- H3: could not parse mutmut counts
- H4: survivors(weakened)=None not > survivors(full)=None (full=None, weakened=None)
- M3: No mutants evaluated (killed + survived = 0)
- M4: no exit=None note=native Windows attempt failed to run: [WinError 2] The system cannot find the file specified

### cosmic-ray
- H1: cosmic-ray install/run check: cosmic-ray, version 8.9.0
- H2: cosmic-ray test command set to pytest -m unit; pytest.ini untouched
- H3: could not parse cosmic-ray counts
- H4: survivors(weakened)=None not > survivors(full)=None (full=None, weakened=None)
- M3: No mutants evaluated (killed + survived = 0)
- M4: no exit=None note=native Windows attempt failed to run: [WinError 2] The system cannot find the file specified

### pytest-gremlins
- H1: pytest-gremlins install/run check: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]
- H2: pytest --gremlins -m unit runs under the existing pytest.ini unchanged
- H3: FAIL: pytest-gremlins has no machine-readable timeout count; H3 requires killed/survived/timeout counts
- H4: survivors(weakened)=None not > survivors(full)=None (full=None, weakened=None)
- M3: No mutants evaluated (killed + survived = 0)
- M4: yes exit=0 note=native Windows host attempt exited 0: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]

## Decision
- Winner: `None`
- Rule outcome: No candidate passed all hard rules (H1-H5)
- Unit threshold: action=stop score=None (No winner score available)

## Consequences
- No mutation-testing tool selected for enforced CI/unit thresholds in this round because no candidate passed H1-H5 on the fixed target under the pinned harness.
- `adr003` remains an opt-in dependency group; normal `uv sync` does not install it.
- A follow-up round must address result parsing/timeout reporting and demo evaluation so H3/H4 can be measured reliably before adopting any tool or threshold.

## Limits
- Single-machine, single-run benchmark on Windows host with Docker Desktop; timings reflect `python:3.12` container execution and host load.
- Harness install/sync result: `install_ok=True`; in-container uv version observed during run was 0.13.7 while host baseline is uv 0.11.8 (per task context).
- H3 failed for all candidates because killed/survived/timeout counts were not parseable or not fully reported by the harness/tool combination used (pytest-gremlins lacks machine-readable timeout counts in this harness).
- H4 failed because demo survivor counts were unparseable for all candidates; weakened-vs-full assertion discrimination was not established by this run.
- M4 native Windows attempts were performed on the host after container run; tools not installed natively reported as unavailable with recorded exit/detail (see per-candidate M4).
- Results are from harness-written `bench/adr003/results/results.json`; no metrics were typed by hand.
- Demo tests remain excluded from normal collection via `pytest.ini` `norecursedirs`.

