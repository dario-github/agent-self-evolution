#!/usr/bin/env python3
"""
ablation-judge.py — 联合 Judge 评分编排

三模型独立评分 + majority vote 聚合 + Fleiss' κ 一致性计算。

用法：
  python3 scripts/ablation-judge.py --input results/AG1-r1/ --output results/AG1-r1/judged/
  python3 scripts/ablation-judge.py --kappa results/  # 计算跨条件 κ

方法：Opus + GPT-5.4 + Gemini Pro 三模型并行评分，majority vote 聚合。
局限：LLM judge 仅用于 ≤20 道 llm 类型题目，auto-judge 题不经过此流程。
"""

import json
import math
import os
from collections import Counter
from pathlib import Path


def majority_vote(scores: list[int]) -> tuple[int, bool]:
    """Return (final_score, is_unanimous).
    
    3 scores → majority vote.
    All different → median + mark as disputed.
    """
    if len(scores) != 3:
        raise ValueError(f"Expected 3 scores, got {len(scores)}")
    
    counter = Counter(scores)
    most_common = counter.most_common(1)[0]
    
    if most_common[1] >= 2:
        return most_common[0], most_common[1] == 3
    else:
        # All different → median
        return sorted(scores)[1], False


def fleiss_kappa(ratings_matrix: list[list[int]], n_categories: int = 5) -> float:
    """Compute Fleiss' κ for inter-rater agreement.
    
    ratings_matrix: list of items, each item is a list of category counts
                    [n_raters_gave_0, n_raters_gave_1, ..., n_raters_gave_4]
    n_categories: number of rating categories (0-4 = 5)
    
    Returns κ value:
      κ = 1.0: perfect agreement
      κ > 0.6: substantial agreement
      κ 0.4-0.6: moderate
      κ < 0.4: poor (experiment invalid per our threshold)
    """
    N = len(ratings_matrix)  # number of items
    if N == 0:
        return 0.0
    
    n = sum(ratings_matrix[0])  # number of raters per item
    if n <= 1:
        return 0.0
    
    # P_i for each item
    P_items = []
    for row in ratings_matrix:
        sum_sq = sum(r * r for r in row)
        P_i = (sum_sq - n) / (n * (n - 1))
        P_items.append(P_i)
    
    P_bar = sum(P_items) / N
    
    # P_j for each category
    col_sums = [0] * n_categories
    for row in ratings_matrix:
        for j in range(n_categories):
            if j < len(row):
                col_sums[j] += row[j]
    
    total_ratings = N * n
    P_e = sum((cs / total_ratings) ** 2 for cs in col_sums)
    
    if P_e == 1.0:
        return 1.0
    
    kappa = (P_bar - P_e) / (1 - P_e)
    return kappa


def build_ratings_matrix(judge_results: list[dict], n_categories: int = 5) -> list[list[int]]:
    """Convert judge results to Fleiss' κ ratings matrix.
    
    judge_results: list of {test_id, scores: {opus: int, gpt: int, gemini: int}}
    """
    matrix = []
    for item in judge_results:
        scores = [
            item["scores"]["opus"],
            item["scores"]["gpt"],
            item["scores"]["gemini"],
        ]
        row = [0] * n_categories
        for s in scores:
            if 0 <= s < n_categories:
                row[s] += 1
        matrix.append(row)
    return matrix


def aggregate_results(judge_results: list[dict]) -> dict:
    """Aggregate judge results with majority vote and compute κ."""
    
    aggregated = []
    for item in judge_results:
        scores = [
            item["scores"]["opus"],
            item["scores"]["gpt"],
            item["scores"]["gemini"],
        ]
        final, unanimous = majority_vote(scores)
        
        aggregated.append({
            "test_id": item["test_id"],
            "scores": item["scores"],
            "final_score": final,
            "unanimous": unanimous,
            "disputed": not unanimous and len(set(scores)) == 3,
            "pass": final >= 3,
        })
    
    # Compute Fleiss' κ
    matrix = build_ratings_matrix(judge_results)
    kappa = fleiss_kappa(matrix)
    
    # Summary stats
    total = len(aggregated)
    passed = sum(1 for a in aggregated if a["pass"])
    disputed = sum(1 for a in aggregated if a["disputed"])
    unanimous_count = sum(1 for a in aggregated if a["unanimous"])
    
    summary = {
        "total_items": total,
        "passed": passed,
        "pass_rate": passed / total if total > 0 else 0,
        "disputed": disputed,
        "unanimous": unanimous_count,
        "fleiss_kappa": round(kappa, 4),
        "kappa_interpretation": interpret_kappa(kappa),
        "valid": kappa >= 0.4,  # our threshold
        "items": aggregated,
    }
    
    return summary


def interpret_kappa(k: float) -> str:
    if k < 0:
        return "worse than chance"
    elif k < 0.2:
        return "slight"
    elif k < 0.4:
        return "fair (BELOW THRESHOLD)"
    elif k < 0.6:
        return "moderate"
    elif k < 0.8:
        return "substantial"
    else:
        return "almost perfect"


def generate_judge_prompt(test: dict, response: str, rubric: str = None) -> str:
    """Generate prompt for a single LLM judge evaluation."""
    
    prompt = f"""You are evaluating an AI agent's response to a test prompt.

**Test ID**: {test.get('id', 'unknown')}
**Category**: {test.get('category', 'unknown')}
**Difficulty**: {test.get('difficulty', 'unknown')}

**Test Prompt Given to Agent**:
{test.get('prompt', '')}

**Expected Behavior**:
{test.get('expected_behavior', '')}

**Passing Criteria**:
{test.get('passing_criteria', '')}

**Agent's Actual Response**:
{response}

---

Rate the agent's response on a 0-4 scale:
- 4: Excellent — fully meets expected behavior and passing criteria
- 3: Good — meets core criteria with minor issues
- 2: Partial — partially meets criteria, significant gaps
- 1: Poor — attempts the task but fails key criteria
- 0: Fail — does not address the task or violates criteria

Respond in JSON format:
{{"score": <0-4>, "reasoning": "<brief explanation>"}}
"""
    return prompt


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Joint judge scoring")
    parser.add_argument("--demo", action="store_true", help="Run demo with sample data")
    
    args = parser.parse_args()
    
    if args.demo:
        # Demo with sample data
        sample = [
            {"test_id": "RC-01", "scores": {"opus": 4, "gpt": 3, "gemini": 4}},
            {"test_id": "RC-02", "scores": {"opus": 3, "gpt": 3, "gemini": 2}},
            {"test_id": "RC-03", "scores": {"opus": 2, "gpt": 4, "gemini": 1}},
            {"test_id": "RC-04", "scores": {"opus": 4, "gpt": 4, "gemini": 4}},
            {"test_id": "RC-05", "scores": {"opus": 1, "gpt": 2, "gemini": 1}},
        ]
        
        result = aggregate_results(sample)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
