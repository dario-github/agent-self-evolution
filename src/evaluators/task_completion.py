#!/usr/bin/env python3
"""M4: Active task progress rate evaluator.

Parses a Markdown table under an `Active Context` section and computes the share
of projects marked as active / progressing / completed.
"""

import argparse
import os

ACTIVE_STATUSES = {"🔨", "🔥", "✅", "in progress", "active", "done"}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True, help="Unused; kept for interface consistency")
    p.add_argument("--memory-file", default=os.path.expanduser("./MEMORY.md"))
    return p.parse_args()


def analyze_active_context(path: str):
    total = 0
    progressing = 0
    in_active_context = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "Active Context" in line and line.startswith("#"):
                    in_active_context = True
                    continue
                if in_active_context:
                    if line.startswith("## ") and "Active Context" not in line:
                        break
                    if not line.startswith("|") or "---" in line:
                        continue
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    if cells and cells[0].lower() in ("项目", "project", "**项目**", "**project**"):
                        continue
                    total += 1
                    row_lower = line.lower()
                    if any(s in line for s in {"🔨", "🔥", "✅"}) or any(s in row_lower for s in {"in progress", "active", "done"}):
                        progressing += 1
    except FileNotFoundError:
        return 0, 0
    return total, progressing


def main():
    args = parse_args()
    total, progressing = analyze_active_context(args.memory_file)
    if total == 0:
        print("0.0")
    else:
        print(f"{progressing / total:.4f}")


if __name__ == "__main__":
    main()
