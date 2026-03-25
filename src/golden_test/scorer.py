#!/usr/bin/env python3
"""
scorer.py — Golden Test Set Tri-Layer Scorer

Implements the Tri-Layer Scoring methodology:
  L1 Trace Check   — Behavior verification via TraceAnalyzer (did it call the right tools?)
  L2 Output Check  — Result verification (keyword/value matching)
  L3 LLM Judge     — Semantic quality evaluation (fallback, ≤20% of tests)

Outputs Markdown reports and structured JSON for analysis.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

from .trace_analyzer import TraceAnalyzer

CATEGORY_LABELS = {
    "memory_retrieval":     "A. Memory Retrieval",
    "rule_compliance":      "B. Rule Compliance",
    "tool_usage":           "C. Tool Usage",
    "multi_step_reasoning": "D. Multi-step Reasoning",
}
DIFF_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}

CATEGORY_STRATEGY = {
    "memory_retrieval": {
        "l1_required": True,
        "l2_required": True,
        "l3_fallback": False,
    },
    "rule_compliance": {
        "l1_required": True,
        "l2_required": False,
        "l3_fallback": True,
    },
    "tool_usage": {
        "l1_required": True,
        "l2_required": False,
        "l3_fallback": False,
    },
    "multi_step_reasoning": {
        "l1_required": True,
        "l2_required": True,
        "l3_fallback": True,
    },
}

# ── L1 Trace Check ────────────────────────────────────────────────────────────

def l1_trace_check(judge_cfg: dict, ta: TraceAnalyzer | None, response: str | None) -> dict:
    if ta is None and response is None:
        return {
            "pass": None,
            "score": 0,
            "checks": [],
            "reason": "No trace and no response found for L1 check",
        }

    if ta is None:
        ta = TraceAnalyzer(response)

    checks: list[dict] = []
    passed = True

    def _chk(kind: str, value: str, found: bool) -> None:
        nonlocal passed
        checks.append({"type": kind, "value": value, "pass": found})
        if not found:
            passed = False

    for tool in judge_cfg.get("auto_match_tool", []):
        _chk("tool_required", tool, ta.has_tool_call(tool))

    for tool in judge_cfg.get("auto_reject_tool", []):
        _chk("tool_forbidden", tool, ta.no_tool_call(tool))

    all_tool_names = " ".join(ta.get_all_tool_names()).lower()
    final_out = ta.get_final_output().lower()
    for behavior in judge_cfg.get("auto_match_behavior", []):
        found_in_trace = behavior.lower() in all_tool_names
        found_in_text  = behavior.lower() in final_out
        _chk("behavior", behavior, found_in_trace or found_in_text)

    failed = [c["value"] for c in checks if not c["pass"]]
    reason = "All trace checks passed" if passed else f"Trace checks failed: {failed}"

    return {
        "pass": passed,
        "score": 1 if passed else 0,
        "checks": checks,
        "reason": reason,
        "tools_observed": ta.get_all_tool_names(),
    }

# ── L2 Output Check ───────────────────────────────────────────────────────────

def l2_output_check(judge_cfg: dict, response: str | None, ta: TraceAnalyzer | None = None) -> dict:
    if response is None:
        return {
            "pass": False,
            "score": 0,
            "checks": [],
            "reason": "Response file missing or empty",
        }

    text = response.lower()
    checks: list[dict] = []
    passed = True

    def _chk(kind: str, value: str, found: bool) -> None:
        nonlocal passed
        checks.append({"type": kind, "value": value, "pass": found})
        if not found:
            passed = False

    for kw in judge_cfg.get("auto_match", []):
        _chk("keyword", kw, kw.lower() in text)

    failed = [c["value"] for c in checks if not c["pass"]]

    if not checks:
        return {
            "pass": True,
            "score": 1,
            "checks": [],
            "reason": "No L2 keyword configurations",
            "neutral": True,
        }

    reason = "All keywords matched" if passed else f"Keywords not matched: {failed}"
    return {
        "pass": passed,
        "score": 5 if passed else 1,
        "checks": checks,
        "reason": reason,
    }

# ── L3 LLM Judge ─────────────────────────────────────────────────────────────

def l3_llm_judge(judge_file: str | None) -> dict:
    if not judge_file or not os.path.exists(judge_file):
        return {
            "pass": None,
            "score": None,
            "reason": f"Judge file not found: {judge_file}",
            "method": "llm",
        }

    with open(judge_file, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if text.startswith("{"):
        try:
            obj = json.loads(text)
            verdict_raw = str(obj.get("verdict", obj.get("pass", ""))).upper()
            verdict = "PASS" if verdict_raw in ("PASS", "TRUE") else "FAIL"
            score = obj.get("score")
            reason = obj.get("reason", obj.get("reasoning", "(No reason)"))[:300]
            return {"pass": verdict == "PASS", "score": score, "reason": reason, "method": "llm", "raw": text[:400]}
        except json.JSONDecodeError:
            pass

    yaml_verdict = re.search(r"^pass\s*:\s*(true|false)", text, re.IGNORECASE | re.MULTILINE)
    yaml_reason  = re.search(r"^reasoning\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if yaml_verdict:
        verdict = "PASS" if yaml_verdict.group(1).lower() == "true" else "FAIL"
        score = 5 if verdict == "PASS" else 1
        reason = yaml_reason.group(1).strip()[:300] if yaml_reason else "(No reasoning)"
        return {"pass": verdict == "PASS", "score": score, "reason": reason, "method": "llm", "raw": text[:400]}

    vm = re.search(r"VERDICT\s*[:\-]\s*(PASS|FAIL)", text, re.IGNORECASE)
    sm = re.search(r"SCORE\s*[:\-]\s*([0-9](?:\.[0-9])?)", text, re.IGNORECASE)
    rm = re.search(r"REASON\s*[:\-]\s*(.+?)(?:\n\n|$)", text, re.IGNORECASE | re.DOTALL)

    if not vm:
        if "pass" in text.lower():
            vm_guess = "PASS"
        else:
            vm_guess = "FAIL"
        return {
            "pass": vm_guess == "PASS",
            "score": None,
            "reason": f"Could not parse VERDICT, inferred: {vm_guess}. Raw: {text[:150]}",
            "method": "llm",
            "raw": text[:400],
        }

    verdict = vm.group(1).upper()
    score   = float(sm.group(1)) if sm else (5 if verdict == "PASS" else 1)
    reason  = rm.group(1).strip()[:300] if rm else "(No reason)"

    return {
        "pass":   verdict == "PASS",
        "score":  score,
        "reason": reason,
        "method": "llm",
        "raw":    text[:400],
    }

def score_test(test: dict, judge_cfg: dict, response: str | None, ta: TraceAnalyzer | None, judge_file: str | None) -> dict:
    category = test.get("category", judge_cfg.get("category", "unknown"))
    judge_type = judge_cfg.get("judge_type", test.get("judge_type", "auto"))

    has_l1_config = bool(judge_cfg.get("auto_match_tool") or judge_cfg.get("auto_reject_tool") or judge_cfg.get("auto_match_behavior"))
    l1_result = None
    if has_l1_config or (ta is not None):
        l1_result = l1_trace_check(judge_cfg, ta, response)

    has_l2_config = bool(judge_cfg.get("auto_match"))
    l2_result = None
    if has_l2_config or judge_type == "auto":
        l2_result = l2_output_check(judge_cfg, response, ta)

    has_llm_judge = bool(judge_cfg.get("judge_prompt")) and judge_type == "llm"

    l1_pass = l1_result["pass"] if l1_result else None
    l2_pass = l2_result["pass"] if l2_result else None

    if l1_pass is not None and l2_pass is not None:
        l2_neutral = l2_result.get("neutral", False)
        if l2_neutral:
            auto_pass = l1_pass
        else:
            auto_pass = l1_pass and l2_pass
    elif l1_pass is not None:
        auto_pass = l1_pass
    elif l2_pass is not None:
        auto_pass = l2_pass
    else:
        auto_pass = None

    l3_result = None
    if has_llm_judge and (auto_pass is False or auto_pass is None):
        l3_result = l3_llm_judge(judge_file)

    if auto_pass is True:
        final_pass = True
        final_reason = _combine_reasons(l1_result, l2_result, None)
        final_method = "auto"
    elif l3_result is not None and l3_result["pass"] is not None:
        final_pass = l3_result["pass"]
        final_reason = l3_result["reason"]
        final_method = "llm"
    elif auto_pass is False:
        final_pass = False
        final_reason = _combine_reasons(l1_result, l2_result, None)
        final_method = "auto"
    else:
        final_pass = None
        final_reason = "Insufficient data to score (missing response/trace/judge files)"
        final_method = "unknown"

    return {
        "pass":         final_pass,
        "score":        5 if final_pass else (1 if final_pass is False else 0),
        "method":       final_method,
        "reason":       final_reason,
        "l1":           l1_result,
        "l2":           l2_result,
        "l3":           l3_result,
        "tools_observed": (l1_result or {}).get("tools_observed", []),
    }

def _combine_reasons(l1: dict | None, l2: dict | None, l3: dict | None) -> str:
    parts = []
    if l1 and l1.get("reason"):
        parts.append(f"L1: {l1['reason']}")
    if l2 and l2.get("reason") and not l2.get("neutral"):
        parts.append(f"L2: {l2['reason']}")
    if l3 and l3.get("reason"):
        parts.append(f"L3: {l3['reason']}")
    return " | ".join(parts) if parts else "(No details)"

def generate_report(plan: dict, results: list) -> str:
    lines = []
    total = len(results)
    passed = sum(1 for r in results if r["judge_result"]["pass"] is True)
    pct = passed / total * 100 if total else 0
    grade = "Good ✅" if pct >= 80 else ("Acceptable ⚠️" if pct >= 60 else "Needs Improvement ❌")

    lines += [
        "# Golden Test Evaluation Report", "",
        f"- **Plan ID**: {plan.get('plan_id', 'unknown')}",
        f"- **Test Model**: {plan.get('test_model', '?')}",
        f"- **Total Score**: **{passed}/{total}** ({pct:.1f}%) — **{grade}**",
        ""
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    pass  # Main CLI block abbreviated for compactness, functions exported for library use
