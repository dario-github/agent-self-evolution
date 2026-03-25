#!/usr/bin/env python3
"""
ablation-runner.py — 消融实验自动化编排

串行切换消融条件，条件内并行 spawn GT 测试 session，
收集响应后恢复原始文件。

用法：
  python3 scripts/ablation-runner.py --plan    # 生成执行计划
  python3 scripts/ablation-runner.py --run     # 执行（需主 session 配合）
  python3 scripts/ablation-runner.py --status  # 查看进度
  python3 scripts/ablation-runner.py --restore # 恢复原始文件

方法：workspace 文件替换实现消融。条件间串行确保隔离，条件内 batch spawn 实现并行。
局限：依赖主 session 执行 spawn（本脚本生成命令，不直接 spawn）。消融期间其他 session 也受影响。

设计决策（为什么不自动 spawn）：
  sessions_spawn 是 OpenClaw tool call，不是 CLI 命令。
  本脚本生成结构化的 plan.json，由主 session 读取并逐条执行 spawn。
  这比 hack CLI 更可靠，也保持了 OpenClaw 的 session 管理一致性。
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.path.expanduser("."))
ABLATION_DIR = Path("/tmp/ablation")
RESULTS_DIR = Path(os.path.expanduser("./memory/evaluation/ablation"))
WORKSPACE_FILES = [
    "AGENTS.md", "SOUL.md", "TOOLS.md",
    "USER.md", "IDENTITY.md", "MEMORY.md", "HEARTBEAT.md"
]

# Experiment parameters
CONDITIONS = ["AG0", "AG1", "AG2", "AG3", "AG4", "AG5", "AG6"]
K_RUNS = 3
BATCH_SIZE = 10  # concurrent spawns within a condition
TEST_MODEL = "anthropic/claude-sonnet-4-6"


def sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_plan(test_file: str = None, randomize: bool = True):
    """Generate execution plan with Latin square randomization."""
    
    # Load test set
    tests_path = Path(test_file) if test_file else RESULTS_DIR / "gt-v4-tests" / "all-tests.yaml"
    if not tests_path.exists():
        print(f"❌ Test file not found: {tests_path}")
        print("  Run GT v4 test generation first.")
        return None
    
    # Randomize condition order (Latin square principle)
    condition_orders = []
    for run_idx in range(K_RUNS):
        order = CONDITIONS.copy()
        if randomize:
            random.seed(42 + run_idx)  # reproducible but different per run
            # Keep AG0 first in run 0 for baseline, randomize rest
            if run_idx == 0:
                rest = order[1:]
                random.shuffle(rest)
                order = [order[0]] + rest
            else:
                random.shuffle(order)
        condition_orders.append(order)
    
    plan = {
        "experiment_id": f"ablation-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "conditions": CONDITIONS,
            "k_runs": K_RUNS,
            "batch_size": BATCH_SIZE,
            "test_model": TEST_MODEL,
            "test_file": str(tests_path),
            "randomized": randomize,
        },
        "condition_orders": condition_orders,
        "steps": [],
        "status": "planned",
    }
    
    step_idx = 0
    for run_idx, order in enumerate(condition_orders):
        for cond in order:
            plan["steps"].append({
                "step_idx": step_idx,
                "run_idx": run_idx,
                "condition": cond,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "file_checksums_before": None,
                "file_checksums_after": None,
            })
            step_idx += 1
    
    # Save plan
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = RESULTS_DIR / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    
    total_steps = len(plan["steps"])
    print(f"✅ Plan generated: {total_steps} steps ({len(CONDITIONS)} conditions × {K_RUNS} runs)")
    print(f"   Condition orders (Latin square):")
    for i, order in enumerate(condition_orders):
        print(f"   Run {i}: {' → '.join(order)}")
    print(f"   Saved to: {plan_path}")
    return plan


def apply_condition(condition: str) -> dict:
    """Apply ablation condition by replacing workspace files.
    Returns checksums of applied files for verification."""
    
    source_dir = ABLATION_DIR / condition
    if not source_dir.exists():
        raise FileNotFoundError(f"Ablation files not found: {source_dir}")
    
    checksums = {}
    for fname in WORKSPACE_FILES:
        src = source_dir / fname
        dst = WORKSPACE / fname
        if src.exists():
            shutil.copy2(src, dst)
            checksums[fname] = sha256(dst)
    
    return checksums


def restore_original() -> dict:
    """Restore original workspace files from AG0 (control)."""
    return apply_condition("AG0")


def verify_restored() -> bool:
    """Verify workspace matches original checksums."""
    checksums_path = ABLATION_DIR / "original-checksums.json"
    if not checksums_path.exists():
        print("❌ Original checksums not found")
        return False
    
    expected = json.loads(checksums_path.read_text())
    all_ok = True
    for fname, expected_hash in expected.items():
        actual = sha256(WORKSPACE / fname)
        if actual != expected_hash:
            print(f"  ❌ {fname}: hash mismatch")
            all_ok = False
    
    return all_ok


def get_status():
    """Show current experiment status."""
    plan_path = RESULTS_DIR / "plan.json"
    if not plan_path.exists():
        print("No plan found. Run --plan first.")
        return
    
    plan = json.loads(plan_path.read_text())
    
    total = len(plan["steps"])
    completed = sum(1 for s in plan["steps"] if s["status"] == "completed")
    running = sum(1 for s in plan["steps"] if s["status"] == "running")
    pending = sum(1 for s in plan["steps"] if s["status"] == "pending")
    
    print(f"Experiment: {plan['experiment_id']}")
    print(f"Status: {plan['status']}")
    print(f"Progress: {completed}/{total} completed, {running} running, {pending} pending")
    
    if running > 0:
        for s in plan["steps"]:
            if s["status"] == "running":
                print(f"  🔄 Step {s['step_idx']}: Run {s['run_idx']} / {s['condition']}")


def generate_step_commands(step_idx: int):
    """Generate spawn commands for a specific step.
    
    Output: JSON array of spawn configs that the main session should execute.
    """
    plan_path = RESULTS_DIR / "plan.json"
    plan = json.loads(plan_path.read_text())
    
    step = plan["steps"][step_idx]
    condition = step["condition"]
    run_idx = step["run_idx"]
    
    # Load test set
    tests_path = Path(plan["parameters"]["test_file"])
    # For now, generate placeholder spawn configs
    # Actual test loading will depend on final test file format
    
    print(f"Step {step_idx}: Run {run_idx} / {condition}")
    print(f"  1. Apply condition: python3 scripts/ablation-gen.py --group {condition} --output /tmp/ablation/{condition}/")
    print(f"  2. Replace workspace: python3 scripts/ablation-runner.py --apply {condition}")
    print(f"  3. Spawn GT tests (batch size {BATCH_SIZE})")
    print(f"  4. Collect responses")
    print(f"  5. Restore: python3 scripts/ablation-runner.py --restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ablation experiment runner")
    parser.add_argument("--plan", action="store_true", help="Generate execution plan")
    parser.add_argument("--test-file", help="Path to test set YAML")
    parser.add_argument("--status", action="store_true", help="Show experiment status")
    parser.add_argument("--apply", metavar="CONDITION", help="Apply a condition (AG0-AG6)")
    parser.add_argument("--restore", action="store_true", help="Restore original files")
    parser.add_argument("--verify", action="store_true", help="Verify restoration")
    parser.add_argument("--step", type=int, help="Generate commands for a step")
    
    args = parser.parse_args()
    
    if args.plan:
        generate_plan(args.test_file)
    elif args.status:
        get_status()
    elif args.apply:
        checksums = apply_condition(args.apply)
        print(f"✅ Applied {args.apply}")
        for f, h in checksums.items():
            print(f"  {f}: {h[:16]}...")
    elif args.restore:
        checksums = restore_original()
        ok = verify_restored()
        print(f"{'✅' if ok else '❌'} Restore {'successful' if ok else 'FAILED'}")
    elif args.verify:
        ok = verify_restored()
        print(f"{'✅' if ok else '❌'} Verification {'passed' if ok else 'FAILED'}")
    elif args.step is not None:
        generate_step_commands(args.step)
    else:
        parser.print_help()
