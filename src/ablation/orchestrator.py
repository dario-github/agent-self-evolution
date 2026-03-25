#!/usr/bin/env python3
"""
ablation-orchestrator.py — 消融实验全自动编排器

完整 pipeline: apply condition → spawn tests → collect → judge → analyze
由主 session exec 运行，通过 OpenClaw HTTP API spawn sub-agents。

用法:
  python3 scripts/ablation-orchestrator.py --pilot     # 10题×2条件 验证
  python3 scripts/ablation-orchestrator.py --full       # 100题×7条件×3轮
  python3 scripts/ablation-orchestrator.py --status     # 查看进度
  python3 scripts/ablation-orchestrator.py --analyze    # 分析结果
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# === Config ===
WORKSPACE = Path(os.path.expanduser("."))
ABLATION_DIR = Path("/tmp/ablation")
RESULTS_BASE = WORKSPACE / "memory" / "evaluation" / "ablation"
TESTS_DIR = RESULTS_BASE / "gt-v4-tests"
PROGRESS_FILE = RESULTS_BASE / "progress.json"

CONDITIONS = ["AG0", "AG1", "AG2", "AG3", "AG4", "AG5", "AG6"]
K_RUNS = 3
TEST_MODEL = "anthropic/claude-sonnet-4-6"
JUDGE_MODELS = ["anthropic/claude-opus-4-6-v1", "openai/gpt-5.4", "google/gemini-3.1-pro-preview"]
SPAWN_CONCURRENCY = 5  # parallel spawns per batch
SPAWN_TIMEOUT = 120    # seconds per test

WORKSPACE_FILES = [
    "AGENTS.md", "SOUL.md", "TOOLS.md",
    "USER.md", "IDENTITY.md", "MEMORY.md", "HEARTBEAT.md"
]


def sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tests():
    """Load all 100 GT v4 tests."""
    tests = []
    for fname in ["all-mr.yaml", "all-rc.yaml", "all-tu.yaml", "all-ms.yaml"]:
        fpath = TESTS_DIR / fname
        with open(fpath) as f:
            items = yaml.safe_load(f)
        tests.extend(items)
    return tests


def apply_condition(condition: str) -> dict:
    """Replace workspace files with ablated versions."""
    src_dir = ABLATION_DIR / condition
    checksums = {}
    for fname in WORKSPACE_FILES:
        src = src_dir / fname
        dst = WORKSPACE / fname
        if src.exists():
            shutil.copy2(src, dst)
            checksums[fname] = sha256(dst)
    return checksums


def restore_workspace():
    """Restore to AG0 (original)."""
    return apply_condition("AG0")


def verify_workspace() -> bool:
    """Verify workspace matches original checksums."""
    cksum_file = ABLATION_DIR / "original-checksums.json"
    if not cksum_file.exists():
        return False
    expected = json.loads(cksum_file.read_text())
    for fname, expected_hash in expected.items():
        actual = sha256(WORKSPACE / fname)
        if actual != expected_hash:
            return False
    return True


def spawn_test(test_item: dict, condition: str, run_idx: int) -> dict:
    """Spawn a single test via OpenClaw sessions_spawn CLI.
    
    Returns dict with test_id, condition, run_idx, response, timing.
    """
    test_id = test_item["id"]
    prompt = test_item["prompt"]
    
    # Build task prompt - clean, just the user query
    task = prompt.strip()
    
    label = f"abl-{condition}-r{run_idx}-{test_id}"
    
    start = time.time()
    try:
        # Use openclaw CLI to spawn
        result = subprocess.run(
            ["openclaw", "session", "spawn",
             "--model", TEST_MODEL,
             "--mode", "run",
             "--timeout", str(SPAWN_TIMEOUT),
             "--label", label,
             "--task", task],
            capture_output=True, text=True, timeout=SPAWN_TIMEOUT + 30
        )
        elapsed = time.time() - start
        
        response = result.stdout.strip() if result.returncode == 0 else f"ERROR: {result.stderr.strip()}"
        
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        response = "ERROR: spawn timeout"
    except Exception as e:
        elapsed = time.time() - start
        response = f"ERROR: {str(e)}"
    
    return {
        "test_id": test_id,
        "condition": condition,
        "run_idx": run_idx,
        "response": response,
        "elapsed_s": round(elapsed, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def judge_auto(test_item: dict, response: str) -> dict:
    """Auto-judge using keyword matching or tool trace."""
    test_id = test_item["id"]
    category = test_item.get("category", "")
    
    if category == "memory_retrieval":
        keywords = test_item.get("expected_keywords", [])
        match_min = test_item.get("match_min", 1)
        matches = sum(1 for kw in keywords if kw.lower() in response.lower())
        
        if matches >= match_min:
            score = 4
        elif matches >= match_min * 0.7:
            score = 3
        elif matches >= match_min * 0.4:
            score = 2
        elif matches > 0:
            score = 1
        else:
            score = 0
        
        return {
            "test_id": test_id,
            "judge_type": "auto_keyword",
            "score": score,
            "detail": f"matched {matches}/{len(keywords)} keywords (min={match_min})",
        }
    
    elif category == "tool_usage":
        expected_tools = test_item.get("expected_tools", [])
        # For tool usage, check if response mentions tool names
        matches = sum(1 for t in expected_tools if t.lower() in response.lower())
        
        if matches >= len(expected_tools):
            score = 4
        elif matches >= len(expected_tools) * 0.7:
            score = 3
        elif matches > 0:
            score = 2
        else:
            score = 0
        
        return {
            "test_id": test_id,
            "judge_type": "auto_tool",
            "score": score,
            "detail": f"matched {matches}/{len(expected_tools)} tools",
        }
    
    # Fallback: needs LLM judge
    return None


def judge_llm(test_item: dict, response: str, model: str) -> dict:
    """LLM-as-judge scoring."""
    test_id = test_item["id"]
    
    rubric = test_item.get("passing_criteria", test_item.get("expected_behavior", ""))
    prompt_text = test_item.get("prompt", "")
    
    judge_prompt = f"""You are evaluating an AI agent's response. Score 0-4.

**Test prompt given to agent:**
{prompt_text}

**Expected behavior / passing criteria:**
{rubric}

**Agent's actual response:**
{response}

**Scoring rubric:**
- 4: Excellent — fully meets expected behavior
- 3: Good — meets core criteria, minor issues  
- 2: Partial — significant gaps
- 1: Poor — attempts but fails key criteria
- 0: Fail — does not address or violates

Output ONLY a JSON object: {{"score": <0-4>, "reasoning": "<1 sentence>"}}"""

    try:
        result = subprocess.run(
            ["openclaw", "session", "spawn",
             "--model", model,
             "--mode", "run",
             "--timeout", "60",
             "--task", judge_prompt],
            capture_output=True, text=True, timeout=90
        )
        
        out = result.stdout.strip()
        # Extract JSON from response
        import re
        match = re.search(r'\{[^}]+\}', out)
        if match:
            parsed = json.loads(match.group())
            return {
                "test_id": test_id,
                "judge_type": "llm",
                "judge_model": model,
                "score": int(parsed.get("score", 0)),
                "reasoning": parsed.get("reasoning", ""),
            }
    except Exception as e:
        pass
    
    return {
        "test_id": test_id,
        "judge_type": "llm",
        "judge_model": model,
        "score": -1,  # error marker
        "reasoning": "judge failed",
    }


def aggregate_judges(judgments: list) -> dict:
    """Aggregate 3-judge panel into final score."""
    scores = [j["score"] for j in judgments if j["score"] >= 0]
    
    if len(scores) == 0:
        return {"final_score": 0, "unanimous": False, "disputed": True}
    
    if len(set(scores)) == 1:
        return {"final_score": scores[0], "unanimous": True, "disputed": False}
    
    # Majority vote
    from collections import Counter
    counts = Counter(scores)
    most_common = counts.most_common(1)[0]
    if most_common[1] >= 2:
        return {"final_score": most_common[0], "unanimous": False, "disputed": False}
    
    # All different → median
    median_score = sorted(scores)[len(scores) // 2]
    return {"final_score": median_score, "unanimous": False, "disputed": True}


def load_progress() -> dict:
    """Load or create progress tracker."""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {
        "experiment_id": f"ablation-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": None,
        "completed_steps": [],
        "current_step": None,
        "results": {},  # condition -> run_idx -> [results]
    }


def save_progress(progress: dict):
    """Save progress tracker."""
    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2))


def run_condition(condition: str, run_idx: int, tests: list, progress: dict):
    """Run all tests for one condition in one run."""
    step_key = f"{condition}_r{run_idx}"
    
    if step_key in progress["completed_steps"]:
        print(f"  ⏭️  {step_key} already completed, skipping")
        return
    
    print(f"\n{'='*60}")
    print(f"  📋 Step: {condition} / Run {run_idx}")
    print(f"  📝 {len(tests)} tests, model={TEST_MODEL}")
    print(f"{'='*60}")
    
    # 1. Apply condition
    print(f"  🔧 Applying {condition}...")
    checksums = apply_condition(condition)
    print(f"  ✅ Applied ({len(checksums)} files)")
    
    # Give a moment for any file watchers
    time.sleep(2)
    
    # 2. Run tests in batches
    all_results = []
    batch_size = SPAWN_CONCURRENCY
    
    for batch_start in range(0, len(tests), batch_size):
        batch = tests[batch_start:batch_start + batch_size]
        batch_end = min(batch_start + batch_size, len(tests))
        print(f"  🚀 Batch {batch_start+1}-{batch_end}/{len(tests)}...")
        
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {
                executor.submit(spawn_test, t, condition, run_idx): t
                for t in batch
            }
            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)
                status = "✅" if not result["response"].startswith("ERROR") else "❌"
                print(f"    {status} {result['test_id']} ({result['elapsed_s']}s)")
    
    # 3. Judge results
    print(f"  ⚖️  Judging {len(all_results)} responses...")
    scored_results = []
    
    for result in all_results:
        test_id = result["test_id"]
        # Find test item
        test_item = next((t for t in tests if t["id"] == test_id), None)
        if not test_item:
            continue
        
        response = result["response"]
        
        # Try auto-judge first
        auto = judge_auto(test_item, response)
        if auto and auto.get("score", -1) >= 0:
            result["score"] = auto["score"]
            result["judge"] = auto
        else:
            # LLM judge panel
            judgments = []
            for model in JUDGE_MODELS:
                j = judge_llm(test_item, response, model)
                judgments.append(j)
            
            agg = aggregate_judges(judgments)
            result["score"] = agg["final_score"]
            result["judge"] = {
                "type": "llm_panel",
                "judgments": judgments,
                **agg,
            }
        
        scored_results.append(result)
        print(f"    📊 {test_id}: {result['score']}/4")
    
    # 4. Save step results
    cond_key = condition
    if cond_key not in progress["results"]:
        progress["results"][cond_key] = {}
    progress["results"][cond_key][str(run_idx)] = scored_results
    progress["completed_steps"].append(step_key)
    save_progress(progress)
    
    # 5. Restore workspace
    print(f"  🔄 Restoring workspace...")
    restore_workspace()
    ok = verify_workspace()
    print(f"  {'✅' if ok else '❌'} Restore {'OK' if ok else 'FAILED'}")
    
    # Summary
    scores = [r["score"] for r in scored_results]
    avg = sum(scores) / len(scores) if scores else 0
    total = sum(scores)
    print(f"  📊 {condition} R{run_idx}: total={total}/{len(scores)*4} avg={avg:.2f}")


def run_pilot():
    """Pilot run: 10 items × AG0 + AG1."""
    print("🧪 === PILOT RUN ===")
    
    tests = load_tests()
    # Sample 10 items (stratified: 3 MR + 3 RC + 2 TU + 2 MS)
    random.seed(42)
    by_cat = {}
    for t in tests:
        cat = t.get("category", "unknown")
        by_cat.setdefault(cat, []).append(t)
    
    pilot_tests = []
    sample_sizes = {"memory_retrieval": 3, "rule_compliance": 3, "tool_usage": 2, "multi_step_reasoning": 2}
    for cat, n in sample_sizes.items():
        pool = by_cat.get(cat, [])
        pilot_tests.extend(random.sample(pool, min(n, len(pool))))
    
    print(f"📋 Pilot: {len(pilot_tests)} tests × 2 conditions (AG0, AG1) × 1 run")
    
    progress = load_progress()
    progress["mode"] = "pilot"
    save_progress(progress)
    
    for condition in ["AG0", "AG1"]:
        run_condition(condition, 0, pilot_tests, progress)
    
    print("\n✅ Pilot complete!")
    print_summary(progress)


def run_full():
    """Full run: 100 items × 7 conditions × 3 runs."""
    print("🔬 === FULL ABLATION RUN ===")
    
    tests = load_tests()
    print(f"📋 Full: {len(tests)} tests × {len(CONDITIONS)} conditions × {K_RUNS} runs = {len(tests)*len(CONDITIONS)*K_RUNS} evaluations")
    
    progress = load_progress()
    progress["mode"] = "full"
    save_progress(progress)
    
    # Latin square ordering
    for run_idx in range(K_RUNS):
        order = CONDITIONS.copy()
        random.seed(42 + run_idx)
        if run_idx == 0:
            rest = order[1:]
            random.shuffle(rest)
            order = [order[0]] + rest
        else:
            random.shuffle(order)
        
        print(f"\n🔄 Run {run_idx}: {' → '.join(order)}")
        
        for condition in order:
            run_condition(condition, run_idx, tests, progress)
    
    print("\n✅ Full run complete!")
    print_summary(progress)


def print_summary(progress: dict):
    """Print experiment summary."""
    print(f"\n{'='*60}")
    print(f"📊 EXPERIMENT SUMMARY")
    print(f"{'='*60}")
    
    for condition in CONDITIONS:
        if condition not in progress["results"]:
            continue
        runs = progress["results"][condition]
        all_scores = []
        for run_idx, results in runs.items():
            scores = [r["score"] for r in results]
            all_scores.extend(scores)
        
        if all_scores:
            avg = sum(all_scores) / len(all_scores)
            total = sum(all_scores)
            n = len(all_scores)
            print(f"  {condition}: avg={avg:.2f} total={total} n={n}")


def show_status():
    """Show current progress."""
    if not PROGRESS_FILE.exists():
        print("No experiment in progress.")
        return
    
    progress = json.loads(PROGRESS_FILE.read_text())
    total_steps = len(CONDITIONS) * K_RUNS if progress["mode"] == "full" else 2
    completed = len(progress["completed_steps"])
    
    print(f"Experiment: {progress['experiment_id']}")
    print(f"Mode: {progress['mode']}")
    print(f"Progress: {completed}/{total_steps} steps")
    print(f"Completed: {', '.join(progress['completed_steps'])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ablation experiment orchestrator")
    parser.add_argument("--pilot", action="store_true", help="Pilot run (10 items × 2 conditions)")
    parser.add_argument("--full", action="store_true", help="Full run (100 × 7 × 3)")
    parser.add_argument("--status", action="store_true", help="Show progress")
    parser.add_argument("--analyze", action="store_true", help="Run analysis on completed results")
    
    args = parser.parse_args()
    
    if args.pilot:
        run_pilot()
    elif args.full:
        run_full()
    elif args.status:
        show_status()
    elif args.analyze:
        # Delegate to ablation-analysis.py
        subprocess.run([sys.executable, str(WORKSPACE / "scripts" / "ablation-analysis.py")])
    else:
        parser.print_help()
