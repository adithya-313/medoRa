# medoRa

## Developer setup

1. Install project dependencies and sync the virtual environment:
   ```bash
   uv sync
   ```

2. Run the test suite:
   ```bash
   uv run pytest -p no:cacheprovider
   ```

3. Run the Phase 0 showcase:
   ```bash
   uv run python -m showcase run --phase 0
   ```

4. Install JavaScript dependencies and run TypeScript/ESLint checks:
   ```bash
   npm ci
   npm run typecheck
   npm run lint
   ```

> [!NOTE]
> `requirements-dev.txt` is kept only for the Phase 0 v1 container (`tools/phase0/run_v1_suite.py` and `run_v1_suite_docker.py`) and is not used for local development tooling.

## Test tiers

Every test must declare exactly one tier marker:

- `unit`: no Docker, network or services (local temp files and local git allowed)
- `integration`: needs real services started in Docker
- `e2e`: full user flow across apps
- `chaos`: fault injection
- `bench`: timing measurement, numbers valid only on the owner's laptop

Run specific tiers:

```bash
uv run pytest -m unit
uv run pytest -m integration
```
(Note: `uv run pytest -m integration` needs Docker running.)
