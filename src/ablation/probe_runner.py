#!/usr/bin/env python3
"""
ablation-probe-runner.py — 消融探针测试集执行器

Phase 1: 格式化+评估函数（NAS）
Phase 2: 通过 ablation-runner.py 编排实际 spawn + 评估（Mac）

用法：
  python3 scripts/ablation-probe-runner.py validate          # 验证 JSON 格式
  python3 scripts/ablation-probe-runner.py evaluate <dir>    # 评估一个条件的结果
  python3 scripts/ablation-probe-runner.py report <dir>      # 生成统计报告
  python3 scripts/ablation-probe-runner.py matrix            # 预期效果矩阵

结果目录结构：
  memory/evaluation/ablation/results/probe-v1/
    AG0/run0/A1-01.json  — 单个探针结果
    AG0/run0/A1-01.response.txt — 原始回复
    ...
    report.json — 汇总报告
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from scipy import stats  # type: ignore

PROBE_FILE = Path(os.path.expanduser(
    "./memory/evaluation/ablation/probe-tests.json"
))
RESULTS_BASE = Path(os.path.expanduser(
    "./memory/evaluation/ablation/results/probe-v1"
))


def load_probes() -> list[dict]:
    data = json.loads(PROBE_FILE.read_text())
    return data["probes"]


# ── Evaluators ──────────────────────────────────────────────

def eval_keyword(response: str, params: dict) -> tuple[int, str]:
    """Check if response contains any of the required keywords."""
    keywords = params.get("keywords", [])
    found = [kw for kw in keywords if kw.lower() in response.lower()]
    if found:
        return 1, f"found: {found}"
    return 0, f"none of {keywords} found"


def eval_keyword_absent(response: str, params: dict) -> tuple[int, str]:
    """Check that response does NOT contain forbidden keywords."""
    forbidden = params.get("forbidden", [])
    found = [kw for kw in forbidden if kw.lower() in response.lower()]
    if found:
        return 0, f"forbidden found: {found}"
    return 1, "no forbidden keywords"


def eval_tool_call(response: str, params: dict,
                   tool_log: list[dict] | None = None) -> tuple[int, str]:
    """Check if expected tools were called (from tool log or response text)."""
    expected = params.get("expected_tools", [])
    if tool_log:
        called = {t.get("name", "") for t in tool_log}
        matched = [t for t in expected if t in called]
        if matched:
            return 1, f"tools called: {matched}"
        return 0, f"expected {expected}, called {called}"
    # Fallback: check response text for tool call indicators
    indicators = expected + [t.replace("_", " ") for t in expected]
    found = [t for t in indicators if t.lower() in response.lower()]
    if found:
        return 1, f"tool indicators in response: {found}"
    return 0, f"no tool indicators for {expected}"


def eval_pattern(response: str, params: dict) -> tuple[int, str]:
    """Check if response matches a regex pattern."""
    pattern = params.get("pattern", "")
    if re.search(pattern, response, re.IGNORECASE):
        return 1, f"matched: {pattern}"
    return 0, f"no match for: {pattern}"


def eval_llm_judge(response: str, params: dict) -> tuple[int, str]:
    """Placeholder for LLM judge evaluation.
    In production, calls GPT-5.4/Opus to judge response quality.
    For now, returns -1 (needs manual/LLM evaluation)."""
    criteria = params.get("criteria", "")
    return -1, f"LLM_JUDGE_NEEDED: {criteria}"


EVALUATORS = {
    "keyword": eval_keyword,
    "keyword_absent": eval_keyword_absent,
    "tool_call": eval_tool_call,
    "pattern": eval_pattern,
    "llm_judge": eval_llm_judge,
}


def evaluate_probe(probe: dict, response: str,
                   tool_log: list[dict] | None = None) -> dict:
    """Evaluate a single probe response."""
    method = probe["eval_method"]
    params = probe.get("eval_params", {})
    evaluator = EVALUATORS.get(method)
    if not evaluator:
        return {"score": -1, "reason": f"unknown method: {method}"}

    if method == "tool_call":
        score, reason = evaluator(response, params, tool_log)
    else:
        score, reason = evaluator(response, params)

    # Check negative keywords if present
    neg_kws = probe.get("negative_keywords", [])
    if score == 1 and neg_kws:
        found_neg = [kw for kw in neg_kws if kw.lower() in response.lower()]
        if found_neg:
            score = 0
            reason += f" | BUT negative keywords found: {found_neg}"

    return {
        "probe_id": probe["id"],
        "condition": probe["condition"],
        "category": probe["category"],
        "eval_method": method,
        "score": score,
        "reason": reason,
    }


# ── Commands ────────────────────────────────────────────────

def cmd_validate():
    """Validate probe JSON format and print summary."""
    probes = load_probes()
    by_condition = defaultdict(list)
    by_category = defaultdict(list)
    by_method = defaultdict(int)

    errors = []
    for p in probes:
        pid = p.get("id", "?")
        if not all(k in p for k in ["id", "condition", "prompt", "eval_method"]):
            errors.append(f"{pid}: missing required fields")
        by_condition[p["condition"]].append(pid)
        by_category[p["category"]].append(pid)
        by_method[p["eval_method"]] += 1

    print(f"📋 Probe Test Set v1")
    print(f"   Total probes: {len(probes)}")
    print(f"\n   By condition:")
    for cond in sorted(by_condition):
        print(f"     {cond}: {len(by_condition[cond])} probes")
    print(f"\n   By category:")
    for cat in sorted(by_category):
        print(f"     {cat}: {len(by_category[cat])} probes")
    print(f"\n   By eval method:")
    for m in sorted(by_method):
        print(f"     {m}: {by_method[m]}")

    if errors:
        print(f"\n   ❌ Errors: {len(errors)}")
        for e in errors:
            print(f"     - {e}")
    else:
        print(f"\n   ✅ All probes valid")


def cmd_evaluate(results_dir: str):
    """Evaluate all probe results in a condition directory."""
    rdir = Path(results_dir)
    if not rdir.exists():
        print(f"❌ Directory not found: {rdir}")
        return

    probes = {p["id"]: p for p in load_probes()}
    results = []

    for resp_file in sorted(rdir.glob("*.response.txt")):
        pid = resp_file.stem.replace(".response", "")
        if pid not in probes:
            print(f"⚠️  Unknown probe: {pid}")
            continue

        response = resp_file.read_text()

        # Try loading tool log
        tool_log = None
        tool_file = rdir / f"{pid}.tools.json"
        if tool_file.exists():
            tool_log = json.loads(tool_file.read_text())

        result = evaluate_probe(probes[pid], response, tool_log)
        results.append(result)

        # Save individual result
        result_file = rdir / f"{pid}.json"
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    # Summary
    scored = [r for r in results if r["score"] >= 0]
    passed = sum(1 for r in scored if r["score"] == 1)
    needs_judge = sum(1 for r in results if r["score"] == -1)
    total = len(scored)

    print(f"\n📊 Evaluation: {rdir.name}")
    print(f"   Scored: {passed}/{total} pass ({passed/total*100:.1f}%)" if total else "   No scored results")
    if needs_judge:
        print(f"   Needs LLM judge: {needs_judge}")

    # Save summary
    summary = {
        "directory": str(rdir),
        "total": len(results),
        "scored": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0,
        "needs_judge": needs_judge,
        "results": results,
    }
    (rdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def cmd_report(base_dir: str = None):
    """Generate cross-condition statistical report."""
    base = Path(base_dir) if base_dir else RESULTS_BASE
    if not base.exists():
        print(f"❌ Results directory not found: {base}")
        return

    probes = load_probes()
    conditions = sorted(set(p["condition"] for p in probes))
    all_conditions = ["AG0"] + conditions  # AG0 is baseline

    # Load all summaries
    summaries = {}
    for cond_dir in base.iterdir():
        if not cond_dir.is_dir():
            continue
        cond = cond_dir.name
        runs = {}
        for run_dir in sorted(cond_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            summary_file = run_dir / "summary.json"
            if summary_file.exists():
                runs[run_dir.name] = json.loads(summary_file.read_text())
        if runs:
            summaries[cond] = runs

    if not summaries:
        print("❌ No results found")
        return

    print(f"\n{'='*60}")
    print(f"📊 消融探针实验报告")
    print(f"{'='*60}")

    # Per-condition aggregate
    for cond in sorted(summaries):
        runs = summaries[cond]
        total_pass = sum(r["passed"] for r in runs.values())
        total_scored = sum(r["scored"] for r in runs.values())
        rate = total_pass / total_scored * 100 if total_scored else 0
        print(f"\n  {cond}: {total_pass}/{total_scored} ({rate:.1f}%) across {len(runs)} runs")

    # Cross-condition comparison for each condition's own probes
    print(f"\n{'─'*60}")
    print(f"探针 × 条件 交叉分析（每个条件在自己的 10 题上）:")
    print(f"{'─'*60}")

    if "AG0" in summaries:
        baseline_results = {}
        for run_name, run_data in summaries["AG0"].items():
            for r in run_data.get("results", []):
                pid = r["probe_id"]
                baseline_results.setdefault(pid, []).append(r["score"])

        for target_cond in conditions:
            if target_cond not in summaries:
                continue
            target_probes = [p["id"] for p in probes if p["condition"] == target_cond]
            bl_scores = []
            tg_scores = []
            for pid in target_probes:
                bl_scores.extend(baseline_results.get(pid, []))
                for run_data in summaries[target_cond].values():
                    for r in run_data.get("results", []):
                        if r["probe_id"] == pid and r["score"] >= 0:
                            tg_scores.append(r["score"])

            if bl_scores and tg_scores:
                bl_pass = sum(bl_scores) / len(bl_scores)
                tg_pass = sum(tg_scores) / len(tg_scores)
                # Fisher exact test on aggregated counts
                a = sum(bl_scores)
                b = len(bl_scores) - a
                c = sum(tg_scores)
                d = len(tg_scores) - c
                _, p_val = stats.fisher_exact([[a, b], [c, d]])
                sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
                print(f"  {target_cond}: baseline {bl_pass:.0%} → ablated {tg_pass:.0%}  "
                      f"Δ={tg_pass-bl_pass:+.0%}  p={p_val:.4f} {sig}")


def cmd_matrix():
    """Print expected effect matrix."""
    probes = load_probes()
    conditions = sorted(set(p["condition"] for p in probes))

    print("\n📐 预期效果矩阵")
    print(f"{'Probe':<8} {'Cond':<5} {'AG0':<5} ", end="")
    for c in conditions:
        print(f"{c:<5} ", end="")
    print()
    print("─" * (18 + 6 * len(conditions)))

    for p in probes:
        print(f"{p['id']:<8} {p['condition']:<5} {'pass':<5} ", end="")
        for c in conditions:
            if c == p["condition"]:
                print(f"{'FAIL':<5} ", end="")
            else:
                print(f"{'pass':<5} ", end="")
        print()


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="消融探针测试集执行器")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("validate", help="验证 JSON 格式")
    ev = sub.add_parser("evaluate", help="评估结果目录")
    ev.add_argument("dir", help="结果目录路径")
    rp = sub.add_parser("report", help="生成统计报告")
    rp.add_argument("dir", nargs="?", help="结果根目录")
    sub.add_parser("matrix", help="预期效果矩阵")

    args = parser.parse_args()

    if args.command == "validate":
        cmd_validate()
    elif args.command == "evaluate":
        cmd_evaluate(args.dir)
    elif args.command == "report":
        cmd_report(args.dir)
    elif args.command == "matrix":
        cmd_matrix()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
