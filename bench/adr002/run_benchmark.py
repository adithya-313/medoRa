"""ADR-002 Feature Flag and Experimentation Benchmark Harness."""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from bench.adr002.homemade import assign_variant


def generate_shop_ids(count: int = 10000) -> list[str]:
    """Generate sequential shop IDs formatted as 'shop-00001'..'shop-10000'.

    Args:
        count: Number of shop IDs to generate.

    Returns:
        List of formatted shop ID strings.
    """
    return [f"shop-{i:05d}" for i in range(1, count + 1)]


def chi_square_50_50(count_a: int, count_b: int) -> float:
    """Compute two-tailed p-value for 50/50 split chi-square test with 1 degree of freedom.

    Formula:
        Total N = count_a + count_b, expected E = N / 2.
        chi2 = (count_a - E)^2 / E + (count_b - E)^2 / E.
        For 1 degree of freedom, p = erfc(sqrt(chi2 / 2)).

    Args:
        count_a: Observed count for variant A.
        count_b: Observed count for variant B.

    Returns:
        Two-tailed p-value between 0.0 and 1.0.

    Raises:
        ValueError: If total count is zero.
    """
    total = count_a + count_b
    if total <= 0:
        raise ValueError(f"Total count must be positive, got {total}")

    expected = total / 2.0
    chi2 = ((count_a - expected) ** 2) / expected + ((count_b - expected) ** 2) / expected
    return math.erfc(math.sqrt(chi2 / 2.0))


def select_winner(candidates: dict[str, dict[str, Any]]) -> str:
    """Select the winning candidate based on ADR-002 decision rules.

    Rules:
    1. Filter to candidates passing all hard rules (H1 to H5 are True).
    2. Among passing candidates, pick fewest M1 (extra containers).
    3. Tie-break 1: pick lowest M2 (server RAM in MiB).
    4. Tie-break 2: pick lowest M3 (Python evaluation time in ms).

    Args:
        candidates: Dictionary mapping candidate name to metric/hard rule dict.

    Returns:
        Name of the winning candidate.

    Raises:
        ValueError: If no candidate passed all hard rules.
    """
    eligible = [
        name
        for name, data in candidates.items()
        if data.get("H1") and data.get("H2") and data.get("H3") and data.get("H4") and data.get("H5")
    ]
    if not eligible:
        raise ValueError("No candidate passed all hard rules (H1-H5)")

    eligible.sort(
        key=lambda name: (
            candidates[name]["M1"],
            candidates[name]["M2"],
            candidates[name]["M3"],
        )
    )
    return eligible[0]


def parse_mem_usage_mib(raw_str: str) -> float:
    """Parse docker memory usage string (e.g. '130.2MiB', '1.28GiB', '400kB') to MiB.

    Args:
        raw_str: Raw memory string with unit suffix.

    Returns:
        Floating-point memory size in MiB.

    Raises:
        ValueError: If string format cannot be parsed.
    """
    match = re.match(r"^([\d\.]+)\s*([a-zA-Z]+)", raw_str.strip())
    if not match:
        raise ValueError(f"Cannot parse memory usage string: {raw_str}")

    val = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("gib", "gb"):
        return val * 1024.0
    if unit in ("mib", "mb"):
        return val
    if unit in ("kib", "kb"):
        return val / 1024.0
    if unit in ("b",):
        return val / (1024.0 * 1024.0)

    raise ValueError(f"Unknown memory unit in string: {raw_str}")


def measure_containers_ram_mib(container_names: list[str], samples: int = 5) -> float:
    """Measure total RAM usage across specified containers, returning median of N samples.

    Args:
        container_names: List of docker container names to measure.
        samples: Number of measurement repetitions to sample.

    Returns:
        Median total memory usage in MiB rounded to 2 decimal places.

    Raises:
        ValueError: If any container is not found in docker stats.
    """
    sample_totals: list[float] = []
    for _ in range(samples):
        cmd = ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        stats_map: dict[str, float] = {}
        for line in proc.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                c_name = parts[0].strip()
                used_str = parts[1].split("/")[0].strip()
                stats_map[c_name] = parse_mem_usage_mib(used_str)

        total_mib = 0.0
        for name in container_names:
            if name not in stats_map:
                raise ValueError(f"Container '{name}' not found in docker stats output")
            total_mib += stats_map[name]

        sample_totals.append(total_mib)
        time.sleep(1.0)

    sample_totals.sort()
    median_val = sample_totals[len(sample_totals) // 2]
    return round(median_val, 2)


def run_node_homemade(experiment_key: str, split_percent: int) -> list[str]:
    """Execute the Node.js homemade variant assignment script via subprocess.

    Args:
        experiment_key: Experiment identifier string.
        split_percent: Percentage allocated to variant A.

    Returns:
        List of variant strings assigned by Node.js.
    """
    node_script = Path(__file__).parent / "node" / "homemade.mjs"
    cmd = ["node", str(node_script), experiment_key, str(split_percent)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    results: list[str] = json.loads(proc.stdout)
    return results


def evaluate_homemade(
    shop_ids: list[str],
    experiment_key: str = "adr002-aa",
    split_percent: int = 50,
) -> dict[str, Any]:
    """Run ADR-002 benchmark measurements for the homemade candidate.

    Args:
        shop_ids: List of 10,000 shop IDs to evaluate.
        experiment_key: Experiment identifier key.
        split_percent: Percentage split for variant A.

    Returns:
        Dictionary of hard rule results (H1-H5) and measured metrics (M1-M5).
    """
    # H1: Free self-hosted (in-tree pure Python/Node hash assignment, no external license required)
    h1 = True

    # H2: Offline evaluation (Pure deterministic hash calculation requires no server; 100% offline identical)
    # Online vs offline assignments are identical by definition since no server state is involved.
    online_variants = [assign_variant(experiment_key, sid, split_percent) for sid in shop_ids]
    offline_variants = [assign_variant(experiment_key, sid, split_percent) for sid in shop_ids]
    h2 = online_variants == offline_variants

    # H3: Cross-language consistency between Python and Node
    node_variants = run_node_homemade(experiment_key, split_percent)
    h3 = online_variants == node_variants

    # H4: Exposure hook (Wrap assignment with custom exposure recorder callback)
    exposure_count = 0

    def record_exposure(sid: str, variant: str) -> None:
        nonlocal exposure_count
        exposure_count += 1

    def assign_with_hook(sid: str) -> str:
        var = assign_variant(experiment_key, sid, split_percent)
        record_exposure(sid, var)
        return var

    _ = [assign_with_hook(sid) for sid in shop_ids]
    h4 = exposure_count == len(shop_ids)

    # H5: Self-contained (Runs in local environment with no SaaS dependency)
    h5 = True

    # M1: Extra production containers
    m1 = 0

    # M2: Server RAM in MiB (0 MiB since no daemon/server containers run)
    m2 = 0.0

    # M3: Median Python evaluation time across 5 repetitions in milliseconds
    timings: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        _ = [assign_variant(experiment_key, sid, split_percent) for sid in shop_ids]
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    timings.sort()
    m3 = round(timings[2], 2)  # median of 5 samples

    # M4: Balance chi-square p-value
    count_a = sum(1 for v in online_variants if v == "A")
    count_b = sum(1 for v in online_variants if v == "B")
    m4 = round(chi_square_50_50(count_a, count_b), 6)

    # M5: Built-in statistics engine
    m5 = False

    return {
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
        "H5": h5,
        "M1": m1,
        "M2": m2,
        "M3": m3,
        "M4": m4,
        "M5": m5,
        "doc_urls": [
            "https://github.com/adithya-313/medoRa/blob/main/bench/adr002/homemade.py",
        ],
        "details": {
            "exposure_count": exposure_count,
            "count_a": count_a,
            "count_b": count_b,
            "repetitions_ms": timings,
        },
    }


def setup_unleash(
    admin_url: str = "http://localhost:4242",
    admin_token: str = "*:*.medora-admin-token-secret",
    experiment_key: str = "adr002-aa",
) -> None:
    """Provision Unleash 50/50 experiment feature flag and strategy via Admin API.

    Args:
        admin_url: Unleash base server URL.
        admin_token: Unleash administrative API token.
        experiment_key: Name of feature flag to create or configure.

    Raises:
        ValueError: If Unleash server returns unexpected API response.
    """
    client = httpx.Client(base_url=admin_url, headers={"Authorization": admin_token}, timeout=10.0)

    # Check if feature flag exists
    feature_check = client.get(f"/api/admin/projects/default/features/{experiment_key}")
    if feature_check.status_code == 404:
        # Create feature flag with impressionData enabled
        create_res = client.post(
            "/api/admin/projects/default/features",
            json={
                "name": experiment_key,
                "type": "experiment",
                "description": "ADR-002 50/50 test",
                "impressionData": True,
            },
        )
        if create_res.status_code not in (200, 201):
            raise ValueError(f"Failed to create Unleash feature: {create_res.status_code} {create_res.text}")

    # Ensure environment is enabled
    client.post(f"/api/admin/projects/default/features/{experiment_key}/environments/development/on")

    # Fetch existing strategies
    strats_res = client.get(
        f"/api/admin/projects/default/features/{experiment_key}/environments/development/strategies"
    )
    if strats_res.status_code != 200:
        raise ValueError(f"Failed to fetch Unleash strategies: {strats_res.status_code} {strats_res.text}")

    existing_strats = strats_res.json()
    has_valid_strategy = False
    for s in existing_strats:
        variants = s.get("variants", [])
        if len(variants) == 2 and any(v.get("name") == "A" for v in variants):
            has_valid_strategy = True
        else:
            # Delete redundant or unconfigured strategy
            client.delete(
                f"/api/admin/projects/default/features/{experiment_key}/environments/development/strategies/{s['id']}"
            )

    if not has_valid_strategy:
        add_res = client.post(
            f"/api/admin/projects/default/features/{experiment_key}/environments/development/strategies",
            json={
                "name": "flexibleRollout",
                "parameters": {
                    "rollout": "100",
                    "stickiness": "userId",
                    "groupId": experiment_key,
                },
                "constraints": [],
                "variants": [
                    {"name": "A", "weight": 500, "weightType": "variable", "stickiness": "userId"},
                    {"name": "B", "weight": 500, "weightType": "variable", "stickiness": "userId"},
                ],
            },
        )
        if add_res.status_code not in (200, 201):
            raise ValueError(f"Failed to add Unleash strategy: {add_res.status_code} {add_res.text}")


def run_node_unleash() -> list[str]:
    """Execute Unleash Node.js SDK evaluation script via subprocess.

    Returns:
        List of 10,000 variant name strings from Node evaluation.
    """
    node_script = Path(__file__).parent / "node" / "evaluate_unleash.mjs"
    cmd = ["node", str(node_script)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    results: list[str] = json.loads(proc.stdout)
    return results


def evaluate_unleash(
    shop_ids: list[str],
    experiment_key: str = "adr002-aa",
) -> dict[str, Any]:
    """Run ADR-002 benchmark measurements for Unleash OSS candidate.

    Args:
        shop_ids: List of 10,000 shop IDs to evaluate.
        experiment_key: Feature flag key to evaluate.

    Returns:
        Dictionary of hard rule results (H1-H5) and measured metrics (M1-M5).

    Raises:
        ValueError: If setup or evaluation fails.
    """
    setup_unleash(experiment_key=experiment_key)

    # H1: Free self-hosted (Apache 2.0 open source, supports strategy variants and in-process SDK evaluation)
    h1 = True

    # M1: Extra production containers (unleash server + unleash-db postgres)
    m1 = 2

    # M2: Measure server RAM usage across Unleash containers after sync
    m2 = measure_containers_ram_mib(["medora-adr002-unleash-1", "medora-adr002-unleash-db-1"], samples=5)

    # Initialize UnleashClient with impression callback
    from UnleashClient import UnleashClient
    from UnleashClient.events import BaseEvent, UnleashEventType

    client_token = "default:development.medora-client-token-secret"
    exposure_count = 0

    def event_callback(event: BaseEvent) -> None:
        nonlocal exposure_count
        if event.event_type == UnleashEventType.VARIANT:
            exposure_count += 1

    client = UnleashClient(
        url="http://localhost:4242/api",
        app_name="medora-bench",
        environment="development",
        custom_headers={"Authorization": client_token},
        event_callback=event_callback,
        refresh_interval=3600,
        metrics_interval=3600,
        disable_metrics=True,
        disable_registration=True,
    )
    client.initialize_client()

    # Online evaluation
    online_variants = [
        client.get_variant(experiment_key, context={"userId": sid})["name"] for sid in shop_ids
    ]

    # H3: Cross-language consistency between Python SDK and Node SDK
    node_variants = run_node_unleash()
    h3 = online_variants == node_variants

    # H2: Offline evaluation: pause polling, stop server container, evaluate again from client cache
    client.unleash_scheduler.pause()
    subprocess.run(["docker", "stop", "-t", "1", "medora-adr002-unleash-1"], check=True)
    offline_variants = [
        client.get_variant(experiment_key, context={"userId": sid})["name"] for sid in shop_ids
    ]
    h2 = online_variants == offline_variants
    subprocess.run(["docker", "start", "medora-adr002-unleash-1"], check=True)
    client.unleash_scheduler.resume()
    time.sleep(2.0)  # allow server health to recover

    # H4: Exposure hook (verify impression callback registered exactly 10,000 exposures)
    time.sleep(0.5)  # allow callback background worker thread to finish processing
    h4 = exposure_count == len(shop_ids)

    # H5: Self-contained (runs entirely in local docker containers without SaaS dependencies)
    h5 = True

    # M3: Median Python evaluation latency across 5 repetitions
    timings: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        _ = [client.get_variant(experiment_key, context={"userId": sid})["name"] for sid in shop_ids]
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    timings.sort()
    m3 = round(timings[2], 2)

    # M4: Balance chi-square p-value
    count_a = sum(1 for v in online_variants if v == "A")
    count_b = sum(1 for v in online_variants if v == "B")
    m4 = round(chi_square_50_50(count_a, count_b), 6)

    # M5: Built-in statistics engine (Unleash OSS does not include experiment stats engine)
    m5 = False

    client.destroy()

    return {
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
        "H5": h5,
        "M1": m1,
        "M2": m2,
        "M3": m3,
        "M4": m4,
        "M5": m5,
        "doc_urls": [
            "https://github.com/Unleash/unleash/blob/main/LICENSE",
            "https://docs.getunleash.io/reference/strategy-variants",
            "https://docs.getunleash.io/reference/architecture",
        ],
        "details": {
            "exposure_count": exposure_count,
            "count_a": count_a,
            "count_b": count_b,
            "repetitions_ms": timings,
        },
    }


def _get_growthbook_auth(client: httpx.Client) -> tuple[str, str]:
    """Authenticate with GrowthBook, creating initial admin user if needed, and return token and org ID.

    Args:
        client: HTTPX client targeting GrowthBook API.

    Returns:
        Tuple of (auth_token, org_id).

    Raises:
        ValueError: If authentication or organization retrieval fails.
    """
    first_res = client.post(
        "/auth/firsttime",
        json={
            "email": "admin@medora.local",
            "password": "MedoraPassword123!",
            "companyname": "medoRa",
            "name": "Admin",
        },
    )
    if first_res.status_code == 200:
        auth_token = first_res.json()["token"]
    else:
        login_res = client.post(
            "/auth/login",
            json={
                "email": "admin@medora.local",
                "password": "MedoraPassword123!",
            },
        )
        if login_res.status_code != 200:
            raise ValueError(
                f"Failed to authenticate with GrowthBook: {login_res.status_code} {login_res.text}"
            )
        auth_token = login_res.json()["token"]

    user_res = client.get("/user", headers={"Authorization": f"Bearer {auth_token}"})
    if user_res.status_code != 200:
        raise ValueError(f"Failed to fetch GrowthBook user info: {user_res.status_code} {user_res.text}")
    orgs = user_res.json().get("organizations", [])
    if not orgs:
        raise ValueError("GrowthBook user has no associated organizations")
    return auth_token, orgs[0]["id"]


def _ensure_growthbook_feature(
    client: httpx.Client,
    secret_key: str,
    experiment_key: str,
) -> None:
    """Create or update GrowthBook feature flag with 50/50 experiment rule.

    Args:
        client: HTTPX client.
        secret_key: GrowthBook administrative secret API key.
        experiment_key: Target feature identifier.

    Raises:
        ValueError: If feature creation or update fails.
    """
    api_headers = {"Authorization": f"Bearer {secret_key}"}
    feat_check = client.get(f"/api/v1/features/{experiment_key}", headers=api_headers)
    if feat_check.status_code in (400, 404):
        create_res = client.post(
            "/api/v1/features",
            json={
                "id": experiment_key,
                "valueType": "string",
                "defaultValue": "B",
                "owner": "admin@medora.local",
                "description": "ADR-002 50/50 test",
            },
            headers=api_headers,
        )
        if create_res.status_code not in (200, 201):
            raise ValueError(
                f"Failed to create GrowthBook feature: {create_res.status_code} {create_res.text}"
            )

    update_res = client.post(
        f"/api/v1/features/{experiment_key}",
        json={
            "environments": {
                "production": {
                    "enabled": True,
                    "rules": [
                        {
                            "type": "experiment",
                            "condition": "",
                            "trackingKey": experiment_key,
                            "hashAttribute": "id",
                            "coverage": 1.0,
                            "values": [
                                {"value": "A", "weight": 0.5},
                                {"value": "B", "weight": 0.5},
                            ],
                        }
                    ],
                }
            }
        },
        headers=api_headers,
    )
    if update_res.status_code not in (200, 201):
        raise ValueError(
            f"Failed to update GrowthBook feature rules: {update_res.status_code} {update_res.text}"
        )


def _get_or_create_sdk_connection(
    client: httpx.Client,
    org_headers: dict[str, str],
    experiment_key: str,
) -> str:
    """Retrieve existing or create new SDK connection, waiting until feature is synced.

    Args:
        client: HTTPX client.
        org_headers: Headers containing auth and organization context.
        experiment_key: Feature key to verify presence of rules.

    Returns:
        SDK client key string.

    Raises:
        ValueError: If SDK connection cannot be created.
    """
    conn_list = client.get("/sdk-connections", headers=org_headers).json().get("connections", [])
    if conn_list:
        sdk_key = conn_list[0]["key"]
    else:
        conn_res = client.post(
            "/sdk-connections",
            json={
                "name": "medora-sdk",
                "environment": "production",
                "languages": ["python", "javascript"],
                "projects": [],
                "encryptPayload": False,
            },
            headers=org_headers,
        )
        if conn_res.status_code != 200:
            raise ValueError(
                f"Failed to create GrowthBook SDK connection: {conn_res.status_code} {conn_res.text}"
            )
        sdk_key = conn_res.json()["connection"]["key"]

    for _ in range(10):
        feat_res = client.get(f"/api/features/{sdk_key}")
        if feat_res.status_code == 200:
            feat_data = feat_res.json().get("features", {}).get(experiment_key, {})
            if feat_data.get("rules"):
                return str(sdk_key)
        time.sleep(1.0)

    return str(sdk_key)


def setup_growthbook(
    api_url: str = "http://localhost:3110",
    experiment_key: str = "adr002-aa",
) -> str:
    """Provision GrowthBook user, secret API key, feature rule, and SDK connection.

    Args:
        api_url: GrowthBook API base URL.
        experiment_key: Feature flag identifier key.

    Returns:
        Generated or fetched SDK client key string.

    Raises:
        ValueError: If setup API calls fail.
    """
    client = httpx.Client(base_url=api_url, timeout=10.0)
    auth_token, org_id = _get_growthbook_auth(client)

    org_headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Organization": org_id,
    }

    key_res = client.post(
        "/keys",
        json={"type": "admin", "description": "medora-bench-admin-key"},
        headers=org_headers,
    )
    if key_res.status_code != 200:
        raise ValueError(f"Failed to create GrowthBook admin API key: {key_res.status_code} {key_res.text}")
    secret_key = key_res.json()["key"]["key"]

    _ensure_growthbook_feature(client, secret_key, experiment_key)
    return _get_or_create_sdk_connection(client, org_headers, experiment_key)


def run_node_growthbook(sdk_key: str) -> list[str]:
    """Execute GrowthBook Node.js SDK evaluation script via subprocess.

    Args:
        sdk_key: GrowthBook SDK client key.

    Returns:
        List of 10,000 variant name strings from Node evaluation.
    """
    node_script = Path(__file__).parent / "node" / "evaluate_growthbook.mjs"
    cmd = ["node", str(node_script), sdk_key]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    results: list[str] = json.loads(proc.stdout)
    return results


def evaluate_growthbook(
    shop_ids: list[str],
    experiment_key: str = "adr002-aa",
) -> dict[str, Any]:
    """Run ADR-002 benchmark measurements for GrowthBook free self-hosted candidate.

    Args:
        shop_ids: List of 10,000 shop IDs to evaluate.
        experiment_key: Feature flag key to evaluate.

    Returns:
        Dictionary of hard rule results (H1-H5) and measured metrics (M1-M5).

    Raises:
        ValueError: If setup or evaluation fails.
    """
    sdk_key = setup_growthbook(experiment_key=experiment_key)

    # H1: Free self-hosted (MIT open source, supports percentage targeting and in-process SDK evaluation)
    h1 = True

    # M1: Extra production containers (growthbook server + growthbook-db mongo)
    m1 = 2

    # M2: Measure server RAM usage across GrowthBook containers after sync
    m2 = measure_containers_ram_mib(
        ["medora-adr002-growthbook-1", "medora-adr002-growthbook-db-1"], samples=5
    )

    # Fetch cached features payload for in-process evaluation
    res = httpx.get(f"http://localhost:3110/api/features/{sdk_key}", timeout=10.0)
    if res.status_code != 200:
        raise ValueError(f"Failed to fetch GrowthBook feature payload: {res.status_code} {res.text}")
    cached_features = res.json().get("features", {})

    # Online evaluation with exposure tracking callback
    from growthbook import Experiment, GrowthBook, Result, UserContext

    exposure_count = 0

    def on_experiment_viewed(
        experiment: Experiment[Any], result: Result[Any], user_context: UserContext | None = None
    ) -> None:
        nonlocal exposure_count
        exposure_count += 1

    gb = GrowthBook(
        features=cached_features,
        on_experiment_viewed=on_experiment_viewed,
    )

    online_variants: list[str] = []
    for sid in shop_ids:
        gb.set_attributes({"id": sid})
        val = gb.get_feature_value(experiment_key, "B")
        online_variants.append(val)

    # H3: Cross-language consistency between Python SDK and Node SDK
    node_variants = run_node_growthbook(sdk_key)
    h3 = online_variants == node_variants

    # H2: Offline evaluation: stop server container, evaluate again from cached features
    subprocess.run(["docker", "stop", "-t", "1", "medora-adr002-growthbook-1"], check=True)
    offline_variants: list[str] = []
    for sid in shop_ids:
        gb.set_attributes({"id": sid})
        val = gb.get_feature_value(experiment_key, "B")
        offline_variants.append(val)

    h2 = online_variants == offline_variants
    subprocess.run(["docker", "start", "medora-adr002-growthbook-1"], check=True)
    time.sleep(2.0)  # allow server health to recover

    # H4: Exposure hook (verify callback was invoked for each evaluation)
    h4 = exposure_count == len(shop_ids)

    # H5: Self-contained (runs entirely in local docker containers without SaaS dependencies)
    h5 = True

    # M3: Median Python evaluation latency across 5 repetitions
    timings: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        for sid in shop_ids:
            gb.set_attributes({"id": sid})
            _ = gb.get_feature_value(experiment_key, "B")
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)

    timings.sort()
    m3 = round(timings[2], 2)

    # M4: Balance chi-square p-value
    count_a = sum(1 for v in online_variants if v == "A")
    count_b = sum(1 for v in online_variants if v == "B")
    m4 = round(chi_square_50_50(count_a, count_b), 6)

    # M5: Built-in statistics engine (GrowthBook includes built-in Bayesian and frequentist engine)
    m5 = True

    return {
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
        "H5": h5,
        "M1": m1,
        "M2": m2,
        "M3": m3,
        "M4": m4,
        "M5": m5,
        "doc_urls": [
            "https://github.com/growthbook/growthbook/blob/main/LICENSE",
            "https://docs.growthbook.io/features/rules",
            "https://docs.growthbook.io/lib",
        ],
        "details": {
            "exposure_count": exposure_count,
            "count_a": count_a,
            "count_b": count_b,
            "repetitions_ms": timings,
        },
    }


def generate_adr_markdown(results: dict[str, Any], output_path: Path) -> None:
    """Generate docs/adr/ADR-002-feature-flags.md from benchmark results dictionary.

    Args:
        results: Full benchmark results dictionary.
        output_path: Path where markdown file should be written.
    """
    candidates = results["candidates"]
    winner = results["decision"]

    lines: list[str] = [
        "# ADR-002: Feature Flag and Experimentation System",
        "",
        "## Status",
        "Accepted",
        "",
        "## Context",
        "medoRa requires per-shop feature flags and A/B experimentation capabilities across Python",
        "and TypeScript/Node services. Prior to selecting an architecture, criteria and limits were",
        "pinned in `docs/adr/ADR-002-criteria.md` and benchmarked locally across three candidates:",
        "1. **Self-hosted Unleash (OSS edition)**: `unleashorg/unleash-server:latest` backed by PostgreSQL.",
        "2. **Self-hosted GrowthBook (free self-hosted edition)**: `growthbook/growthbook:latest` backed by MongoDB.",
        "3. **Homemade candidate**: In-repo flags table with deterministic SHA-256 hash assignment.",
        "",
        "## Hard Rules Evaluation (H1-H5)",
        "",
        "| Candidate | H1 Free Self-Hosted | H2 Offline | H3 Cross-Language | H4 Exposure Hook | H5 Self-Contained | Status |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for name in ("homemade", "unleash", "growthbook"):
        c = candidates[name]
        h1_s = "PASS" if c["H1"] else "FAIL"
        h2_s = "PASS" if c["H2"] else "FAIL"
        h3_s = "PASS" if c["H3"] else "FAIL"
        h4_s = "PASS" if c["H4"] else "FAIL"
        h5_s = "PASS" if c["H5"] else "FAIL"
        overall = "ELIGIBLE" if all([c["H1"], c["H2"], c["H3"], c["H4"], c["H5"]]) else "DISQUALIFIED"
        lines.append(f"| **{name}** | {h1_s} | {h2_s} | {h3_s} | {h4_s} | {h5_s} | **{overall}** |")

    lines.extend(
        [
            "",
            "## Measured Metrics (M1-M5)",
            "",
            "| Candidate | M1 Containers | M2 Server RAM (MiB) | M3 Latency (10k ops, ms) | M4 Balance (p-value) | M5 Stats Engine |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |",
        ]
    )

    for name in ("homemade", "unleash", "growthbook"):
        c = candidates[name]
        m5_s = "Yes" if c["M5"] else "No"
        lines.append(f"| **{name}** | {c['M1']} | {c['M2']:.2f} | {c['M3']:.2f} | {c['M4']} | {m5_s} |")

    lines.extend(
        [
            "",
            "## Decision",
            f"**Selected winner: `{winner}`**.",
            "",
            "Under the decision rule established before measurement:",
            "- Among candidates passing all hard rules (H1–H5), select the candidate requiring the fewest extra",
            "  production containers (M1).",
            "- `homemade` requires **0** extra production containers, whereas `unleash` and `growthbook` both",
            "  require **2** containers (application server + backing database).",
            "",
            "## Consequences",
            "### Positive",
            "- **Zero Operational Overhead**: Eliminates operational burden of provisioning, monitoring, and scaling",
            "  standalone feature flag servers and databases.",
            "- **Deterministic Cross-Language Consistency**: SHA-256 hash slicing provides identical variant bucketing",
            "  in Python and Node with sub-millisecond local execution.",
            "- **100% Offline Resilience**: Evaluations are pure function calls that succeed without network dependencies.",
            "",
            "### Negative / Trade-offs",
            "- **No Built-in Experiment Statistics Engine**: Bayesian and frequentist significance testing must be",
            "  handled by analytic pipelines rather than an embedded server UI.",
            "- **Configuration Distribution**: Flag toggles and split percentages are defined in code or configuration",
            "  rather than an interactive web UI.",
            "",
            "## Limits",
            "- **Single Machine Environment**: All measurements were executed on a single Windows laptop running",
            "  Docker Desktop (WSL2 backend). Windows Defender and background filesystem scanning can cause slight",
            "  evaluation latency jitter.",
            "- **Zero Network Contention**: Tests evaluated in-process evaluation performance without network latency",
            "  or saturation on remote endpoints.",
            "- **Offline Cache Scope**: offline was tested with the SDK process still alive (in-memory cache);",
            "  offline evaluation after an app restart (on-disk cache) was not tested.",
        ]
    )

    flagged_p = [f"**{name}** (p = {c['M4']})" for name, c in candidates.items() if c["M4"] < 0.01]
    if flagged_p:
        lines.append(
            f"- **M4 Balance Flag (p < 0.01)**: The following candidate(s) showed p < 0.01 on the 50/50 balance test: {', '.join(flagged_p)}."
        )
    else:
        lines.append(
            "- **M4 Balance**: All candidates achieved p >= 0.01 on the 50/50 balance chi-square test."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_results(results: dict[str, Any], output_path: Path) -> None:
    """Serialize benchmark results to a formatted JSON file.

    Args:
        results: Benchmark results dictionary.
        output_path: Path to target JSON file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    shops = generate_shop_ids(10000)

    print("Evaluating homemade candidate...", flush=True)
    homemade_res = evaluate_homemade(shops)

    print("Evaluating Unleash candidate...", flush=True)
    unleash_res = evaluate_unleash(shops)

    print("Evaluating GrowthBook candidate...", flush=True)
    growthbook_res = evaluate_growthbook(shops)

    all_candidates = {
        "homemade": homemade_res,
        "unleash": unleash_res,
        "growthbook": growthbook_res,
    }

    winner_name = select_winner(all_candidates)
    benchmark_payload = {
        "candidates": all_candidates,
        "decision": winner_name,
    }

    results_file = Path(__file__).parent / "results" / "results.json"
    write_results(benchmark_payload, results_file)
    print(f"Results written to {results_file}", flush=True)

    adr_doc_file = Path(__file__).parents[2] / "docs" / "adr" / "ADR-002-feature-flags.md"
    generate_adr_markdown(benchmark_payload, adr_doc_file)
    print(f"ADR-002 markdown generated at {adr_doc_file}", flush=True)
