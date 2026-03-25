#!/usr/bin/env python3
"""
runner.py — Golden Test Set Runner

Generates test plans and manages execution mapping for Golden Test Set evaluations.

Usage:
  python -m agent_self_evolution.golden_test.runner --model claude-3-sonnet --batch-size 4
"""

import json
import os
import sys
from pathlib import Path

TASKS_DIR = "/tmp/gt-v4/tasks"
JUDGE_DIR = "/tmp/gt-v4/judge_config"
OUTPUT_DIR = "/tmp/gt-v4/responses"
SESSION_DIR = "/tmp/gt-v4/sessions"

def generate_plan(model: str = "claude-3-sonnet", k: int = 1):
    """Generate a plan.json for scoring from tasks/ and judge_config/.
    
    Args:
        model: Model to test against
        k: Number of independent runs per test (for Max@K analysis).
           k=1 is standard single-run. k=3 recommended for capability diagnostics.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)
    
    tasks_files = sorted(Path(TASKS_DIR).glob("*.json"))
    tests = []
    
    for tf in tasks_files:
        with open(tf) as f:
            task = json.load(f)
        
        tid = task["id"]
        for run_idx in range(1, k + 1):
            run_suffix = f"_r{run_idx}" if k > 1 else ""
            tests.append({
                "test_id": f"{tid}{run_suffix}",
                "base_test_id": tid,
                "run_index": run_idx,
                "category": task.get("category", "unknown"),
                "difficulty": task.get("difficulty", "medium"),
                "original_prompt": task.get("prompt", ""),
                "response_file": f"{OUTPUT_DIR}/{tid}{run_suffix}-response.md",
                "session_file": None,
                "judge": {
                    "type": "auto",
                }
            })
    
    plan = {
        "plan_id": f"gt-v4-{'maxk' if k > 1 else 'baseline'}-{model.split('/')[-1]}",
        "test_model": model,
        "judge_model": "gpt-4o",
        "version": "v4",
        "k": k,
        "tests": tests,
    }
    
    plan_path = "/tmp/gt-v4/plan.json"
    with open(plan_path, "w") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    
    n_unique = len(tasks_files)
    print(f"Plan generated: {n_unique} tests × k={k} = {len(tests)} runs → {plan_path}")
    return plan


def update_session_mapping(test_id: str, session_id: str, mapping_path: str = "/tmp/gt-v4/session-mapping.json"):
    """After spawn completes, record session_id for JSONL lookup."""
    mapping = {}
    if os.path.exists(mapping_path):
        with open(mapping_path) as f:
            mapping = json.load(f)
    
    mapping[test_id] = session_id
    
    with open(mapping_path, "w") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def build_session_dir_from_mapping(mapping_path: str = "/tmp/gt-v4/session-mapping.json", sessions_base: str = "/tmp/sessions"):
    """Copy/symlink session JSONLs to SESSION_DIR based on mapping."""
    if not os.path.exists(mapping_path):
        print("No session mapping found")
        return
    
    with open(mapping_path) as f:
        mapping = json.load(f)
    
    os.makedirs(SESSION_DIR, exist_ok=True)
    
    found = 0
    for tid, sid in mapping.items():
        candidates = list(Path(sessions_base).glob(f"{sid}*.jsonl"))
        candidates = [c for c in candidates if ".deleted" not in str(c)]
        
        if candidates:
            src = candidates[0]
            dst = Path(SESSION_DIR) / f"{tid}.jsonl"
            if not dst.exists():
                os.symlink(src, dst)
            found += 1
        else:
            print(f"Warning: {tid}: session {sid} JSONL not found in {sessions_base}")
    
    print(f"Linked {found}/{len(mapping)} session JSONLs to {SESSION_DIR}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate-plan", action="store_true")
    parser.add_argument("--model", default="claude-3-sonnet")
    parser.add_argument("--k", type=int, default=1, help="Runs per test for Max@K analysis")
    parser.add_argument("--record", nargs=2, metavar=("TEST_ID", "SESSION_ID"))
    parser.add_argument("--link-sessions", action="store_true")
    parser.add_argument("--sessions-base", default="/tmp/sessions", help="Base directory for session logs")
    args = parser.parse_args()
    
    if args.generate_plan:
        generate_plan(args.model, k=args.k)
    elif args.record:
        update_session_mapping(args.record[0], args.record[1])
        print(f"Recorded {args.record[0]} → {args.record[1]}")
    elif args.link_sessions:
        build_session_dir_from_mapping(sessions_base=args.sessions_base)
    else:
        parser.print_help()
