"""Experiment results inspection notebook (interactive script format)."""

# %% [markdown]
# # Experiment Results & A/A Test Validation
#
# This notebook loads and inspects empirical results from the homemade feature flags
# and experiment assignment engine (`ml/experiments/results/aa_test.json`).

# %%
import json
from pathlib import Path

results_file = Path("ml/experiments/results/aa_test.json")
if not results_file.exists():
    raise FileNotFoundError(
        f"Results file not found at {results_file}. Run 'python -m ml.experiments.aa_test' first."
    )

data = json.loads(results_file.read_text(encoding="utf-8"))
print(f"Loaded experiment results generated with SEED = {data['seed']}")

# %% [markdown]
# ## 1. Single A/A Experiment Evaluation
#
# Evaluates a 1,000-shop cohort allocated to identical control/variant distributions.

# %%
single = data["single_run"]
print("Single Run Summary:")
print(f"  Cohort size: {single['n_a'] + single['n_b']} shops")
print(f"  Variant A count: {single['n_a']} (mean: {single['mean_a']:.4f}, var: {single['var_a']:.4f})")
print(f"  Variant B count: {single['n_b']} (mean: {single['mean_b']:.4f}, var: {single['var_b']:.4f})")
print(f"  Sample Ratio Mismatch (SRM) p-value: {single['srm_p_value']:.4f}")
print(f"  Welch's t-test difference p-value:    {single['welch_p_value']:.4f}")

if single["srm_p_value"] >= 0.01:
    print("  -> SRM Check: PASSED (no sample allocation mismatch)")
else:
    print("  -> SRM Check: FAILED (sample ratio mismatch detected)")

if single["welch_p_value"] >= 0.05:
    print("  -> A/A Invariance Check: PASSED (no spurious difference detected at alpha=0.05)")
else:
    print("  -> A/A Invariance Check: FAILED (Type I error at alpha=0.05)")

# %% [markdown]
# ## 2. Repeated A/A Test Validation (1,000 Repetitions)
#
# Verifies Type I error calibration across 1,000 independent A/A experiments.

# %%
reps = data["repetitions"]
print("1,000 Repetitions A/A Validation:")
print(f"  Total repetitions: {reps['num_reps']}")
print(f"  Shops per repetition: {reps['num_shops_per_rep']}")
print(f"  Nominal alpha: {reps['alpha']}")
print(
    f"  Welch test false positive rate: {reps['welch_false_positive_rate']:.2%} "
    f"({reps['welch_false_positive_count']} / {reps['num_reps']})"
)
print(
    f"  SRM test false positive rate:   {reps['srm_false_positive_rate']:.2%} "
    f"({reps['srm_false_positive_count']} / {reps['num_reps']})"
)

# In an A/A test, the empirical false positive rate should be close to nominal alpha (5%).
assert 0.02 <= reps["welch_false_positive_rate"] <= 0.08, "False positive rate deviates from nominal 5%"
print("  -> Type I Error Calibration: VERIFIED within theoretical tolerance [2%, 8%]")
