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
print(f"Commit: {data['git_commit']}, Timestamp: {data['timestamp_utc']}")

# %% [markdown]
# ## 1. Single A/A Experiment Evaluation ('aa-single')
#
# Evaluates a 10,000-shop cohort allocated to identical control/variant distributions.

# %%
single = data["single_run"]
print("Single Run Summary:")
print(f"  Cohort size: {single['count_a'] + single['count_a_copy']} shops")
print(f"  Variant A count: {single['count_a']} (mean: {single['mean_a']:.4f}, var: {single['var_a']:.4f})")
print(
    f"  Variant A-copy count: {single['count_a_copy']} "
    f"(mean: {single['mean_a_copy']:.4f}, var: {single['var_a_copy']:.4f})"
)
print(f"  Sample Ratio Mismatch (SRM) p-value: {single['srm_p_value']:.4f}")
print(f"  Welch's t-test difference p-value:    {single['welch_p_value']:.4f}")
print(f"  Single SRM Pass: {single['single_srm_pass']}")
print(f"  Single Welch Pass: {single['single_welch_pass']}")
print(f"  Single Pass: {single['single_pass']}")

# %% [markdown]
# ## 2. Repeated A/A Test Validation (1,000 Repetitions)
#
# Verifies Type I error calibration across 1,000 independent A/A experiments.

# %%
reps = data["repetitions"]
print("1,000 Repetitions A/A Validation:")
print(f"  Total repetitions: {reps['num_reps']}")
print(f"  Welch nominal alpha: {reps['welch_alpha']}")
print(f"  SRM nominal alpha: {reps['srm_alpha']}")
print(
    f"  Welch test false positive rate: {reps['welch_false_positive_rate']:.2%} "
    f"({reps['welch_false_positive_count']} / {reps['num_reps']})"
)
print(
    f"  SRM test false positive rate:   {reps['srm_false_positive_rate']:.2%} "
    f"({reps['srm_false_positive_count']} / {reps['num_reps']})"
)
print(f"  Repetition Welch Pass: {reps['repetition_welch_pass']}")
print(f"  Repetition SRM Pass: {reps['repetition_srm_pass']}")
print(f"  Repetition Pass: {reps['repetition_pass']}")
print(f"  Overall Pass: {data['overall_pass']}")

assert data["overall_pass"] is True, "A/A experiment suite did not achieve overall pass"
