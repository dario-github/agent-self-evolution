#!/usr/bin/env python3
"""M5: Scheduled task silence rate evaluator.

Computes the share of scheduled jobs that appear inactive for more than 7 days,
based on a plain-text listing exported by an external scheduler.
"""

import argparse
import re
from datetime import datetime, timedelta


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="-", help="Scheduler listing file, or - for stdin")
    return p.parse_args()


def evaluate(text: str):
    lines = text.strip().split("\n")
    total = 0
    silent = 0
    now = datetime.utcnow()
    threshold = now - timedelta(days=7)
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("Name") or "───" in line or "---" in line:
            continue
        parts = re.split(r"\s{2,}|\t", line)
        if len(parts) < 1:
            continue
        name = parts[0].strip()
        if not name or name.lower() in ("name", "schedule", "status"):
            continue
        total += 1
        date_match = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})", line)
        if date_match:
            try:
                last_run = datetime.fromisoformat(date_match.group(1).replace(" ", "T"))
                if last_run < threshold:
                    silent += 1
            except ValueError:
                silent += 1
        else:
            if any(k in line.lower() for k in ["error", "never", "disabled", "idle"]):
                silent += 1
    return 0.0 if total == 0 else silent / total


def main():
    args = parse_args()
    if args.input == "-":
        import sys
        text = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    print(f"{evaluate(text):.4f}")


if __name__ == "__main__":
    main()
