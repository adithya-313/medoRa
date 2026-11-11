# medoRa

## Developer setup

Run the following commands to set up the development environment, execute tests, and run the Phase 0 showcase:

```sh
uv sync
uv run pytest -p no:cacheprovider
uv run python -m showcase run --phase 0
```

Note: `requirements-dev.txt` is kept only for the Phase 0 v1 container.


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

> [!NOTE]
> `requirements-dev.txt` is kept only for the Phase 0 v1 container (`tools/phase0/run_v1_suite.py` and `run_v1_suite_docker.py`) and is not used for local development tooling.
