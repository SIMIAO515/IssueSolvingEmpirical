#!/usr/bin/env python3
"""
Compute Stage-1 success rates and paired McNemar comparisons from result.json.

Expected input format:
- metadata.configs: list of configuration names
- instances[instance_id].by_config[config_name]: "success" or "fail"

This script prints:
1) success counts/rates for each configuration
2) McNemar paired comparisons against a baseline config
3) paired Wald 95% CI for the difference in success rates

It also writes:
- stage1_summary.csv
- stage1_mcnemar.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


SUCCESS = "success"
FAIL = "fail"


def chi_square_1df_sf(x: float) -> float:
    """Survival function for chi-square with 1 degree of freedom."""
    if x < 0:
        raise ValueError("chi-square statistic must be non-negative")
    return math.erfc(math.sqrt(x / 2.0))


def mcnemar_pvalue_continuity_corrected(b: int, c: int) -> float:
    """
    Two-sided McNemar test with continuity correction.

    b = baseline fail, variant success
    c = baseline success, variant fail
    statistic = (|b-c|-1)^2 / (b+c)
    """
    if b + c == 0:
        return 1.0
    stat = (abs(b - c) - 1) ** 2 / (b + c)
    return chi_square_1df_sf(stat)


def paired_diff_ci_wald(b: int, c: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """
    95% CI for paired difference in success rates:
        diff = (b - c) / n

    Uses the standard paired Wald variance:
        Var(diff) = ((b+c) - (b-c)^2 / n) / n^2

    Returned values are proportions, not percentages.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    diff = (b - c) / n
    var = ((b + c) - ((b - c) ** 2 / n)) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return diff - z * se, diff + z * se


def load_results(path: Path) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))

    metadata = data.get("metadata", {})
    configs = metadata.get("configs")
    if not configs:
        raise ValueError("metadata.configs is missing or empty")

    instances_raw = data.get("instances")
    if not isinstance(instances_raw, dict) or not instances_raw:
        raise ValueError("instances is missing or empty")

    instances: Dict[str, Dict[str, str]] = {}
    errors: List[str] = []

    for instance_id, item in instances_raw.items():
        by_config = item.get("by_config", {})
        if set(by_config.keys()) != set(configs):
            missing = sorted(set(configs) - set(by_config.keys()))
            extra = sorted(set(by_config.keys()) - set(configs))
            errors.append(f"{instance_id}: by_config mismatch; missing={missing}, extra={extra}")

        for cfg, status in by_config.items():
            if status not in {SUCCESS, FAIL}:
                errors.append(f"{instance_id}: invalid status for {cfg}: {status!r}")

        # Optional consistency check against success/fail arrays.
        success_list = set(item.get("success", []))
        fail_list = set(item.get("fail", []))
        by_success = {cfg for cfg, status in by_config.items() if status == SUCCESS}
        by_fail = {cfg for cfg, status in by_config.items() if status == FAIL}
        if success_list != by_success or fail_list != by_fail:
            errors.append(f"{instance_id}: success/fail lists inconsistent with by_config")

        instances[instance_id] = by_config

    if errors:
        preview = "\n".join(errors[:20])
        raise ValueError(f"Validation failed with {len(errors)} error(s). First errors:\n{preview}")

    return configs, instances


def compute_summary(configs: Iterable[str], instances: Dict[str, Dict[str, str]]) -> List[dict]:
    n = len(instances)
    rows = []
    for cfg in configs:
        resolved = sum(1 for result in instances.values() if result[cfg] == SUCCESS)
        rows.append({
            "configuration": cfg,
            "resolved": resolved,
            "total": n,
            "success_rate": resolved / n,
            "success_rate_percent": 100 * resolved / n,
        })
    return rows


def compute_mcnemar(configs: Iterable[str], instances: Dict[str, Dict[str, str]], baseline: str) -> List[dict]:
    n = len(instances)
    rows = []

    for variant in configs:
        if variant == baseline:
            continue

        n00_both_fail = 0
        n01_baseline_fail_variant_success = 0  # b
        n10_baseline_success_variant_fail = 0  # c
        n11_both_success = 0

        for result in instances.values():
            base_success = result[baseline] == SUCCESS
            var_success = result[variant] == SUCCESS

            if not base_success and not var_success:
                n00_both_fail += 1
            elif not base_success and var_success:
                n01_baseline_fail_variant_success += 1
            elif base_success and not var_success:
                n10_baseline_success_variant_fail += 1
            else:
                n11_both_success += 1

        b = n01_baseline_fail_variant_success
        c = n10_baseline_success_variant_fail
        diff = (b - c) / n
        ci_low, ci_high = paired_diff_ci_wald(b, c, n)
        p_value = mcnemar_pvalue_continuity_corrected(b, c)

        variant_resolved = b + n11_both_success
        baseline_resolved = c + n11_both_success

        rows.append({
            "comparison": f"{baseline}_vs_{variant}",
            "baseline": baseline,
            "variant": variant,
            "baseline_resolved": baseline_resolved,
            "variant_resolved": variant_resolved,
            "baseline_success_rate": baseline_resolved / n,
            "variant_success_rate": variant_resolved / n,
            "diff_variant_minus_baseline": diff,
            "diff_percent": 100 * diff,
            "n00_both_fail": n00_both_fail,
            "n01_baseline_fail_variant_success": b,
            "n10_baseline_success_variant_fail": c,
            "n11_both_success": n11_both_success,
            "mcnemar_p_value_continuity_corrected": p_value,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "ci_low_percent": 100 * ci_low,
            "ci_high_percent": 100 * ci_high,
        })

    return rows


def write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows to write for {path}")
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_summary(summary_rows: List[dict]) -> None:
    print("\nSuccess rates")
    print("| Configuration | Resolved / Total | Success Rate |")
    print("|---|---:|---:|")
    for row in summary_rows:
        print(
            f"| {row['configuration']} | "
            f"{row['resolved']} / {row['total']} | "
            f"{row['success_rate_percent']:.1f}% |"
        )


def print_mcnemar(rows: List[dict]) -> None:
    print("\nMcNemar comparisons")
    print("| Comparison | b: Base fail / Var success | c: Base success / Var fail | Diff | p-value | 95% CI |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['comparison']} | "
            f"{row['n01_baseline_fail_variant_success']} | "
            f"{row['n10_baseline_success_variant_fail']} | "
            f"{row['diff_percent']:+.1f}% | "
            f"{row['mcnemar_p_value_continuity_corrected']:.3f} | "
            f"[{row['ci_low_percent']:+.2f}%, {row['ci_high_percent']:+.2f}%] |"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_json", type=Path, help="Path to result.json")
    parser.add_argument("--baseline", default="Baseline", help="Baseline configuration name")
    parser.add_argument("--out-dir", type=Path, default=Path("."), help="Directory for output CSV files")
    args = parser.parse_args()

    configs, instances = load_results(args.result_json)
    if args.baseline not in configs:
        raise ValueError(f"Baseline {args.baseline!r} not found in configs: {configs}")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = compute_summary(configs, instances)
    mcnemar_rows = compute_mcnemar(configs, instances, args.baseline)

    write_csv(args.out_dir / "stage1_summary.csv", summary_rows)
    write_csv(args.out_dir / "stage1_mcnemar.csv", mcnemar_rows)

    print(f"Loaded {len(instances)} instances and {len(configs)} configurations.")
    print_summary(summary_rows)
    print_mcnemar(mcnemar_rows)
    print(f"\nWrote: {args.out_dir / 'stage1_summary.csv'}")
    print(f"Wrote: {args.out_dir / 'stage1_mcnemar.csv'}")


if __name__ == "__main__":
    main()
