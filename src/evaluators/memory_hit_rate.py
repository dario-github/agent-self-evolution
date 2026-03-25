#!/usr/bin/env python3
"""Agent 能力评估 — M1: Memory 命中率

命中定义：session 中调用了 memory_search 后，同一 session 内又有 read memory/ 的操作
命中率 = 有后续 read 的 memory_search session 数 / 有 memory_search 的 session 数

用法: python3 scripts/eval-memory-hit-rate.py --since 2026-03-01 [--sessions-dir path]
输出: 浮点数 0.0-1.0
"""
import argparse, json, os, glob, re
from datetime import datetime

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--since', required=True, help='YYYY-MM-DD')
    p.add_argument('--sessions-dir', default=os.path.expanduser('~/.openclaw/agents/main/sessions'))
    return p.parse_args()

def session_date(path):
    """Extract date from session filename or first entry. Fallback to mtime."""
    fname = os.path.basename(path)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', fname)
    if m:
        return m.group(1)
    # Fallback: read first line timestamp
    try:
        with open(path, 'r') as f:
            first = f.readline().strip()
            if first:
                d = json.loads(first)
                ts = d.get('timestamp', '')
                if ts and len(ts) >= 10:
                    return ts[:10]
    except:
        pass
    # Fallback: file mtime
    try:
        from datetime import datetime
        mtime = os.path.getmtime(path)
        return datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d')
    except:
        pass
    return None

def analyze_session(path):
    """Returns (has_memory_search, has_follow_up_read)"""
    has_search = False
    has_read = False
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get('type') != 'message':
                        continue
                    msg = d.get('message', {})
                    content = msg.get('content', '')
                    
                    # Check for memory_search tool call
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                if block.get('type') == 'toolCall' and block.get('name') == 'memory_search':
                                    has_search = True
                                if block.get('type') == 'toolCall' and block.get('name') == 'read':
                                    args = block.get('arguments', '')
                                    if 'memory/' in str(args) or 'memory\\/' in str(args):
                                        has_read = True
                                if block.get('type') == 'toolCall' and block.get('name') == 'memory_get':
                                    has_read = True
                except json.JSONDecodeError:
                    continue
    except Exception:
        return False, False
    return has_search, has_read

def main():
    args = parse_args()
    since = args.since
    sessions = glob.glob(os.path.join(args.sessions_dir, '*.jsonl'))
    
    total_with_search = 0
    total_with_hit = 0
    
    for path in sessions:
        date = session_date(path)
        if date and date < since:
            continue
        
        has_search, has_read = analyze_session(path)
        if has_search:
            total_with_search += 1
            if has_read:
                total_with_hit += 1
    
    if total_with_search == 0:
        print("0.0")
    else:
        rate = total_with_hit / total_with_search
        print(f"{rate:.4f}")

if __name__ == '__main__':
    main()
