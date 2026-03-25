#!/usr/bin/env python3
"""
splitter.py — Test Case Splitter

Splits a YAML test set into isolated task prompts and judge configurations
to prevent test leakage.
"""

import json
import os
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    yaml = None

def split_test(test: dict) -> tuple[dict, dict]:
    test_id = test.get("id", "unknown")
    task = {
        "test_id": test_id,
        "category": test.get("category", "unknown"),
        "difficulty": test.get("difficulty", "medium"),
        "prompt": test.get("prompt", ""),
    }

    judge_raw = test.get("judge", "auto")
    if isinstance(judge_raw, str):
        judge_type = judge_raw
        judge_config_extra = {}
    elif isinstance(judge_raw, dict):
        judge_type = judge_raw.get("type", "auto")
        judge_config_extra = {k: v for k, v in judge_raw.items() if k != "type"}
    else:
        judge_type = "auto"
        judge_config_extra = {}

    judge_config = {
        "test_id": test_id,
        "category": test.get("category", "unknown"),
        "difficulty": test.get("difficulty", "medium"),
        "judge_type": judge_type,
        "expected": test.get("expected", ""),
        "judge_prompt": test.get("judge_prompt", ""),
        "auto_match": test.get("auto_match", []),
        "auto_match_tool": test.get("auto_match_tool", []),
        "auto_reject_tool": test.get("auto_reject_tool", []),
        "auto_match_behavior": test.get("auto_match_behavior", []),
        "tags": test.get("tags", []),
    }
    judge_config.update(judge_config_extra)
    return task, judge_config

def process_file(input_path: str, output_dir: str):
    if not yaml:
        print("PyYAML required")
        sys.exit(1)
        
    with open(input_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    tests = data.get("tests", [])
    tasks_dir = os.path.join(output_dir, "tasks")
    judge_dir = os.path.join(output_dir, "judge_config")
    
    os.makedirs(tasks_dir, exist_ok=True)
    os.makedirs(judge_dir, exist_ok=True)
    
    for test in tests:
        test_id = test.get("id", "unknown")
        task, judge = split_test(test)
        
        with open(os.path.join(tasks_dir, f"{test_id}.json"), "w") as f:
            json.dump(task, f, indent=2)
            
        with open(os.path.join(judge_dir, f"{test_id}.json"), "w") as f:
            json.dump(judge, f, indent=2)
            
    print(f"Split {len(tests)} tests to {output_dir}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        process_file(sys.argv[1], sys.argv[2])
