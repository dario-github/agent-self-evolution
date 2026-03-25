#!/usr/bin/env python3
"""M3: Tool success rate evaluator."""

import argparse
import glob
import json
import os
import re

ERROR_PATTERNS = re.compile(
    r'"error"|"status":\s*"error"|ENOENT|EACCES|command not found|'
    r'TypeError|SyntaxError|ModuleNotFoundError|FileNotFoundError|'
    r'401 Unauthorized|403 Forbidden|404 Not Found|500 Internal|timeout|ETIMEDOUT',
    re.IGNORECASE,
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True)
    p.add_argument("--sessions-dir", default=os.path.expanduser("~/.agent/sessions"))
    return p.parse_args()


def session_date(path: str):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return m.group(1) if m else None


def analyze_session(path: str):
    total = 0
    errors = 0
    last_was_toolcall = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                except Exception:
                    continue
                if d.get("type") != "message":
                    continue
                msg = d.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "assistant" and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            total += 1
                            last_was_toolcall = True
                is_tool_result = role == "toolResult"
                if not is_tool_result and role == "user" and isinstance(content, list):
                    is_tool_result = any(
                        isinstance(block, dict) and block.get("type") == "toolResult"
                        for block in content
                    )
                if is_tool_result and last_was_toolcall:
                    text = json.dumps(content, ensure_ascii=False) if isinstance(content, (list, dict)) else str(content)
                    if ERROR_PATTERNS.search(text):
                        errors += 1
                    last_was_toolcall = False
    except Exception:
        pass
    return total, errors


def main():
    args = parse_args()
    total_calls = 0
    total_errors = 0
    for path in glob.glob(os.path.join(args.sessions_dir, "*.jsonl")):
        date = session_date(path)
        if date and date < args.since:
            continue
        calls, errs = analyze_session(path)
        total_calls += calls
        total_errors += errs
    if total_calls == 0:
        print("1.0")
    else:
        print(f"{(total_calls - total_errors) / total_calls:.4f}")


if __name__ == "__main__":
    main()
