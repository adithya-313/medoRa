# ADR-003 Criteria: Mutation-Testing Tool Selection

## Purpose

Choose a mutation-testing tool for medoRa's Python unit-tested code. To ensure objectivity, all criteria and decision rules are fixed before executing measurements.

## Candidates

1. **mutmut** (latest version uv resolves)
2. **cosmic-ray** (latest version uv resolves)
3. **pytest-gremlins** (latest version uv resolves)

## Scope (fixed)

**Files under mutation (target):**
- `cloud/experiments/assignment.py`
- `cloud/experiments/analysis.py`
- `tools/ci/test_change_gate.py`

**Tests used to kill mutants:** the unit tests for those files only, selected with `-m unit`:
- `tests/cloud/test_experiment_assignment.py`
- `tests/cloud/test_experiment_analysis.py`
- `tests/cloud/test_change_gate.py` (unit tests of the change gate)

No integration, e2e, chaos, or bench tests are mutated in Phase 1.

## Hard Rules (disqualifying)

Any candidate that fails any of the following criteria is disqualified:

* **H1: Installs and runs in Linux with uv**: The tool installs via the `adr003` uv dependency group into a Linux container (official `python:3.12` image) and executes a mutation run against the fixed target without source changes.
* **H2: Works with our pytest config unchanged**: The tool honors `pythonpath = .`, the `tests.support.tier_markers` plugin, and `pytest -m unit` selection exactly as configured in `pytest.ini`/`conftest.py`, with no edits to those files.
* **H3: Machine-readable killed/survived/timeout counts**: The tool emits a machine-readable report (JSON or equivalent) containing counts of killed mutants, survived mutants, and timeouts.
* **H4: Weakened-assertion demo**: On `bench/adr003/demo`, the survived-mutant count with the weakened test file is strictly greater than with the full test file (`survivors(weakened) > survivors(full)`).
* **H5: Free OSS licence**: The tool is distributed under a free open-source licence, backed by an official licence URL.

## Measured Metrics

All timings are wall-clock seconds, measured inside the `python:3.12` Docker container with the repo mounted, as the median of 3 runs:

* **M1: Wall-clock seconds**: Median duration of the mutation run for the fixed target (3 runs).
* **M2: Number of mutants**: Total mutants generated for the fixed target.
* **M3: Mutation score**: `killed / (killed + survived)`, where timeouts are counted as killed and stated as such. Expressed as a percentage.
* **M4: Runs natively on Windows**: yes/no (informational / tie-break only; not a hard rule).

## Decision Rule

1. Filter to candidates passing all hard rules (**H1–H5**).
2. Among passing candidates, select the candidate with the lowest **M1**.
3. **Tie-break**: If two or more passing candidates have M1 within 20% of the fastest M1, prefer the candidate with **M4 = yes**. If still tied, prefer the lowest M1.

## Minimum Scores per Tier (rule)

* **unit**: The winner's M3 rounded DOWN to the nearest 5 percentage points, never below 70. If the winner's M3 < 70: STOP, no threshold is set.
* **integration / e2e / chaos / bench**: No mutation run in Phase 1, with the reasons:
  * Docker-per-mutant execution is too slow for integration suites.
  * No e2e or chaos tests exist yet.
  * Bench measures wall-clock performance, not test-suite strength.

## Documentation References

* mutmut: https://mutmut.readthedocs.io/en/latest/ (BSD-3-Clause: https://github.com/boxed/mutmut/blob/main/LICENSE)
* cosmic-ray: https://cosmic-ray.readthedocs.io/en/latest/ (MIT: https://github.com/sixty-north/cosmic-ray/blob/main/LICENCE.txt)
* pytest-gremlins: https://pytest-gremlins.readthedocs.io/en/latest/ (MIT: https://github.com/mikelane/pytest-gremlins/blob/main/LICENSE)
