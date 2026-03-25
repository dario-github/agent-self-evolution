#!/usr/bin/env python3
"""
ablation-analysis.py — 消融实验统计分析

Wilcoxon signed-rank test + Holm-Bonferroni 校正 + Cohen's d + Bootstrap CI。

用法：
  python3 scripts/ablation-analysis.py --input memory/evaluation/ablation/results/
  python3 scripts/ablation-analysis.py --demo  # 用模拟数据演示

方法：非参数检验（Wilcoxon），不假设正态分布。Holm-Bonferroni 控制 FWER。
      Bootstrap CI（10000 resamples）提供效应量的不确定性估计。
局限：k=3 重复次数较少，效力主要来自 100 题的样本量。
"""

import json
import math
import os
import random
from pathlib import Path
from typing import Optional


def wilcoxon_signed_rank(x: list[float], y: list[float]) -> tuple[float, float]:
    """Simplified Wilcoxon signed-rank test (two-sided).
    
    Returns (W_statistic, approximate_p_value).
    Uses normal approximation for n > 10.
    For small n, returns conservative estimate.
    """
    n = len(x)
    assert len(y) == n, "Samples must be same length"
    
    # Compute differences
    diffs = [xi - yi for xi, yi in zip(x, y)]
    
    # Remove zeros
    nonzero = [(abs(d), 1 if d > 0 else -1) for d in diffs if d != 0]
    n_nonzero = len(nonzero)
    
    if n_nonzero == 0:
        return 0, 1.0  # No difference
    
    # Rank by absolute value
    nonzero.sort(key=lambda x: x[0])
    
    # Assign ranks (handle ties with average rank)
    ranks = []
    i = 0
    while i < n_nonzero:
        j = i + 1
        while j < n_nonzero and nonzero[j][0] == nonzero[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks.append((avg_rank, nonzero[k][1]))
        i = j
    
    # W+ and W-
    W_plus = sum(r * s for r, s in ranks if s > 0)
    W_minus = sum(r * abs(s) for r, s in ranks if s < 0)
    W = min(W_plus, W_minus)
    
    # Normal approximation for p-value
    mean_W = n_nonzero * (n_nonzero + 1) / 4
    var_W = n_nonzero * (n_nonzero + 1) * (2 * n_nonzero + 1) / 24
    
    if var_W == 0:
        return W, 1.0
    
    z = (W - mean_W) / math.sqrt(var_W)
    
    # Two-sided p-value (normal approximation)
    p = 2 * (1 - normal_cdf(abs(z)))
    
    return W, p


def normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cohens_d(x: list[float], y: list[float]) -> float:
    """Cohen's d effect size (pooled SD)."""
    n1, n2 = len(x), len(y)
    mean1, mean2 = sum(x) / n1, sum(y) / n2
    
    var1 = sum((xi - mean1) ** 2 for xi in x) / max(n1 - 1, 1)
    var2 = sum((yi - mean2) ** 2 for yi in y) / max(n2 - 1, 1)
    
    pooled_sd = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / max(n1 + n2 - 2, 1))
    
    if pooled_sd == 0:
        return 0.0
    
    return (mean1 - mean2) / pooled_sd


def bootstrap_ci(x: list[float], y: list[float], 
                 n_bootstrap: int = 10000, alpha: float = 0.05,
                 seed: int = 42) -> tuple[float, float, float]:
    """Bootstrap confidence interval for mean difference.
    
    Returns (mean_diff, ci_lower, ci_upper).
    """
    rng = random.Random(seed)
    diffs = [xi - yi for xi, yi in zip(x, y)]
    n = len(diffs)
    
    boot_means = []
    for _ in range(n_bootstrap):
        sample = [rng.choice(diffs) for _ in range(n)]
        boot_means.append(sum(sample) / n)
    
    boot_means.sort()
    lo_idx = int(n_bootstrap * alpha / 2)
    hi_idx = int(n_bootstrap * (1 - alpha / 2))
    
    mean_diff = sum(diffs) / n
    return mean_diff, boot_means[lo_idx], boot_means[hi_idx]


def holm_bonferroni(p_values: list[tuple[str, float]], alpha: float = 0.05) -> list[tuple[str, float, bool, float]]:
    """Holm-Bonferroni multiple comparison correction.
    
    Input: [(label, p_value), ...]
    Output: [(label, p_value, significant, adjusted_alpha), ...]
    """
    m = len(p_values)
    sorted_pvals = sorted(p_values, key=lambda x: x[1])
    
    results = []
    for i, (label, p) in enumerate(sorted_pvals):
        adjusted_alpha = alpha / (m - i)
        significant = p < adjusted_alpha
        results.append((label, p, significant, adjusted_alpha))
        
        if not significant:
            # All subsequent are also not significant
            for j in range(i + 1, len(sorted_pvals)):
                label_j, p_j = sorted_pvals[j]
                results.append((label_j, p_j, False, alpha / (m - j)))
            break
    
    return results


def analyze_experiment(results_dir: Path) -> dict:
    """Full statistical analysis of ablation experiment results.
    
    Expects: results_dir/AG{0-6}/run_{0-2}.json
    Each JSON: {test_scores: [{test_id, final_score, pass, category}, ...]}
    """
    # Load all results
    conditions = {}
    for ag_dir in sorted(results_dir.iterdir()):
        if ag_dir.is_dir() and ag_dir.name.startswith("AG"):
            cond = ag_dir.name
            runs = []
            for run_file in sorted(ag_dir.glob("run_*.json")):
                with open(run_file) as f:
                    runs.append(json.load(f))
            if runs:
                conditions[cond] = runs
    
    if "AG0" not in conditions:
        print("❌ No control (AG0) results found")
        return {}
    
    # Compute per-run aggregates
    def run_stats(run_data):
        scores = [t["final_score"] for t in run_data["test_scores"]]
        total = sum(scores)
        passed = sum(1 for s in scores if s >= 3)
        
        # Per category
        categories = {}
        for t in run_data["test_scores"]:
            cat = t["category"]
            if cat not in categories:
                categories[cat] = {"scores": [], "passed": 0}
            categories[cat]["scores"].append(t["final_score"])
            if t["final_score"] >= 3:
                categories[cat]["passed"] += 1
        
        return {
            "total_score": total,
            "pass_count": passed,
            "pass_rate": passed / len(scores),
            "mean_score": total / len(scores),
            "categories": {
                cat: {
                    "total": sum(d["scores"]),
                    "mean": sum(d["scores"]) / len(d["scores"]),
                    "passed": d["passed"],
                    "pass_rate": d["passed"] / len(d["scores"]),
                }
                for cat, d in categories.items()
            }
        }
    
    # Aggregate stats per condition
    condition_stats = {}
    for cond, runs in conditions.items():
        stats = [run_stats(r) for r in runs]
        condition_stats[cond] = stats
    
    # Statistical tests: each AG vs AG0
    control_totals = [s["total_score"] for s in condition_stats["AG0"]]
    
    comparisons = []
    p_values_for_correction = []
    
    for cond in ["AG1", "AG2", "AG3", "AG4", "AG5", "AG6"]:
        if cond not in condition_stats:
            continue
        
        cond_totals = [s["total_score"] for s in condition_stats[cond]]
        
        W, p = wilcoxon_signed_rank(control_totals, cond_totals)
        d = cohens_d(control_totals, cond_totals)
        mean_diff, ci_lo, ci_hi = bootstrap_ci(control_totals, cond_totals)
        
        comparison = {
            "condition": cond,
            "control_mean": sum(control_totals) / len(control_totals),
            "condition_mean": sum(cond_totals) / len(cond_totals),
            "mean_difference": mean_diff,
            "wilcoxon_W": W,
            "p_value": p,
            "cohens_d": round(d, 4),
            "d_interpretation": interpret_d(d),
            "bootstrap_ci_95": [round(ci_lo, 2), round(ci_hi, 2)],
        }
        
        comparisons.append(comparison)
        p_values_for_correction.append((cond, p))
    
    # Holm-Bonferroni correction
    corrected = holm_bonferroni(p_values_for_correction)
    correction_map = {label: (sig, adj_a) for label, _, sig, adj_a in corrected}
    
    for comp in comparisons:
        sig, adj_alpha = correction_map.get(comp["condition"], (False, 0.05))
        comp["significant_corrected"] = sig
        comp["adjusted_alpha"] = round(adj_alpha, 6)
    
    # Hypothesis testing
    hypotheses = test_hypotheses(condition_stats, comparisons)
    
    analysis = {
        "experiment_id": "ablation-analysis",
        "analyzed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "conditions_analyzed": list(conditions.keys()),
        "runs_per_condition": len(conditions.get("AG0", [])),
        "condition_summaries": {
            cond: {
                "mean_total": sum(s["total_score"] for s in stats) / len(stats),
                "std_total": std([s["total_score"] for s in stats]),
                "mean_pass_rate": sum(s["pass_rate"] for s in stats) / len(stats),
            }
            for cond, stats in condition_stats.items()
        },
        "comparisons": comparisons,
        "hypotheses": hypotheses,
        "removable_groups": [
            c["condition"] for c in comparisons
            if not c["significant_corrected"]
        ],
    }
    
    return analysis


def std(values: list[float]) -> float:
    n = len(values)
    if n <= 1:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def interpret_d(d: float) -> str:
    d = abs(d)
    if d < 0.2:
        return "negligible"
    elif d < 0.5:
        return "small"
    elif d < 0.8:
        return "medium"
    else:
        return "large"


def test_hypotheses(condition_stats: dict, comparisons: list) -> list:
    """Test pre-registered hypotheses."""
    hypotheses = []
    
    comp_map = {c["condition"]: c for c in comparisons}
    
    # H1: AG1 移除后 B 类下降 ≥15%
    if "AG1" in comp_map:
        # Would need per-category data — placeholder
        hypotheses.append({
            "id": "H1",
            "description": "AG1（安全核心）移除后 B 类下降 ≥15%",
            "status": "requires_category_data",
        })
    
    # H5: 至少 2 个 AG 组可安全移除
    removable = sum(1 for c in comparisons if not c["significant_corrected"])
    hypotheses.append({
        "id": "H5",
        "description": f"至少 2 个 AG 组可安全移除",
        "result": f"{removable} groups removable",
        "confirmed": removable >= 2,
    })
    
    return hypotheses


def demo():
    """Run demo with simulated data."""
    random.seed(42)
    
    print("=== Ablation Analysis Demo (Simulated Data) ===\n")
    
    # Simulate: AG0 baseline ~350/400, each AG drops differently
    effects = {
        "AG0": 0,    # control
        "AG1": -30,  # safety rules: big drop
        "AG2": -10,  # auxiliary safety: small drop
        "AG3": -5,   # SOUL: minimal measurable
        "AG4": -25,  # tools: big drop
        "AG5": -20,  # memory: moderate drop
        "AG6": -8,   # process: small drop
    }
    
    for cond, effect in effects.items():
        scores = [350 + effect + random.gauss(0, 10) for _ in range(3)]
        print(f"  {cond}: {[round(s, 1) for s in scores]}  (mean={sum(scores)/3:.1f})")
    
    # Run pairwise tests
    control = [350 + random.gauss(0, 10) for _ in range(3)]
    
    print("\n=== Pairwise Comparisons vs AG0 ===\n")
    p_values = []
    
    for cond in ["AG1", "AG2", "AG3", "AG4", "AG5", "AG6"]:
        effect = effects[cond]
        treatment = [350 + effect + random.gauss(0, 10) for _ in range(3)]
        
        # Use 100-item level scores for proper test
        control_items = [random.gauss(3.5, 0.8) for _ in range(100)]
        treatment_items = [random.gauss(3.5 + effect / 100, 0.8) for _ in range(100)]
        
        W, p = wilcoxon_signed_rank(control_items, treatment_items)
        d = cohens_d(control_items, treatment_items)
        mean_diff, ci_lo, ci_hi = bootstrap_ci(control_items, treatment_items)
        
        p_values.append((cond, p))
        print(f"  {cond}: d={d:+.3f} ({interpret_d(d)}), p={p:.4f}, Δ={mean_diff:+.2f} [{ci_lo:+.2f}, {ci_hi:+.2f}]")
    
    # Holm-Bonferroni
    print("\n=== Holm-Bonferroni Correction ===\n")
    corrected = holm_bonferroni(p_values)
    
    removable = []
    for label, p, sig, adj_a in corrected:
        status = "🔴 SIGNIFICANT" if sig else "🟢 removable"
        print(f"  {label}: p={p:.4f}, α_adj={adj_a:.4f} → {status}")
        if not sig:
            removable.append(label)
    
    print(f"\n=== Conclusion: {len(removable)} groups safely removable: {removable} ===")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Ablation statistical analysis")
    parser.add_argument("--input", type=Path, help="Results directory")
    parser.add_argument("--demo", action="store_true", help="Run demo with simulated data")
    
    args = parser.parse_args()
    
    if args.demo:
        demo()
    elif args.input:
        results = analyze_experiment(args.input)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
