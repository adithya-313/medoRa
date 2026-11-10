# ADR-001 Criteria: Python Dependency Manager Selection

## Context and Purpose

Phase 1 of medoRa requires selecting a standard Python dependency manager to replace ad-hoc dependency management.
To prevent bias, this document establishes the binding evaluation criteria and decision rules **prior** to executing any benchmarks or collecting performance measurements.

## Evaluation Rules

### Hard Rules (Disqualifying)
Any candidate tool that fails any of the following four hard criteria is disqualified from adoption:

* **H1: Reproducible Lock**  
  Locking twice from a clean state from the identical input specification must produce byte-identical lock files (verified by computing and comparing the SHA-256 digest of both lock files).
* **H2: Cryptographic Hashes**  
  The lock file must record cryptographic integrity hashes (e.g. SHA-256) for every resolved package artifact.
* **H3: Host Compatibility (Windows)**  
  Both lock generation and package installation into a target virtual environment must execute cleanly (exit code 0) on this Windows host machine without manual workarounds.
* **H4: Python Version Pinning**  
  The Python version (3.12) must be explicitly pinned and enforced in the project configuration file or the generated lock file.

### Scored Performance Metrics
Candidate tools that satisfy all four hard rules (H1–H4) are evaluated across 5 interleaved repetitions using the median wall-clock time:

1. **Warm Install Duration (Primary Metric)**:  
   Median wall-clock seconds to install dependencies into a fresh, empty virtual environment while preserving the tool's package cache immediately after a successful cold run.
2. **Cold Install Duration (Secondary Metric)**:  
   Median wall-clock seconds to install dependencies into a fresh, empty virtual environment immediately after completely clearing the tool's package cache using its documented cache-cleaning command.
3. **Lock Duration (Informational Metric)**:  
   Median wall-clock seconds to resolve and generate the lock file from the unpinned specification.

### Decision Rule

1. Only candidate tools satisfying **H1, H2, H3, and H4** are eligible for selection.
2. Among eligible candidates, the candidate with the **lowest median warm install duration** is selected.
3. **Tie-Break Policy**: If the top candidates' median warm install times are within 10% of the fastest candidate's time (`(time - min_time) / min_time <= 0.10`), the tie is broken by selecting the candidate with the **lowest median cold install duration**.

### Baseline Reference
Plain `pip` with an unpinned `requirements.in` file is measured strictly as an unmanaged baseline reference. It does not produce a lock file and is expected to fail H1 and H2; it cannot win the evaluation.
