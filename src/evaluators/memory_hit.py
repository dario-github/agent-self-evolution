#!/usr/bin/env python3
"""M1: Memory hit rate evaluator.

Definition:
A session counts as a hit if it first calls a retrieval tool and then reads or
uses the retrieved memory/context in the same session.
"""

import argparse
import glob
import json
import os
import re
from datetime import datetime


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True, help="YYYY-MM-DD")
    p.add_argument("--sessions-dir", default=os.path.expanduser("~/.agent/sessions"))
    return p.parse_args()


def session_date(path: str):
    fname = os.path.basename(path)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", fname)
    if m:
        return m.group(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            if first:
                d = json.loads(first)
                ts = d.get("timestamp", "")
                if len(ts) >= 10:
                    return ts[:10]
    except Exception:
        pass
    try:
        return datetime.utcfromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except Exception:
        return None


def analyze_session(path: str):
    has_search = False
    has_read = False
    retrieval_tools = {"memory_search", "search_memory", "retrieve_context"}
    read_tools = {"read", "memory_get", "fetch_context"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "message":
                    continue
                msg = d.get("message", {})
                content = msg.get("content", "")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "toolCall":
                        continue
                    name = block.get("name", "")
                    if name in retrieval_tools:
                        has_search = True
                    if name in read_tools:
                        has_read = True
                        continue
                    args = str(block.get("arguments", ""))
                    if name == "read" and any(token in args.lower() for token in ["memory/", "context/"]):
                        has_read = True
    except Exception:
        return False, False
    return has_search, has_read


def main():
    args = parse_args()
    total_with_search = 0
    total_with_hit = 0
    for path in glob.glob(os.path.join(args.sessions_dir, "*.jsonl")):
        date = session_date(path)
        if date and date < args.since:
            continue
        has_search, has_read = analyze_session(path)
        if has_search:
            total_with_search += 1
            if has_read:
                total_with_hit += 1
    if total_with_search == 0:
        print("0.0")
    else:
        print(f"{total_with_hit / total_with_search:.4f}")


if __name__ == "__main__":
    main()
