#!/usr/bin/env python3
"""Agent 能力评估 — M3: 工具成功率

成功定义：toolCall 后的 toolResult 不含 error/exception 字样
成功率 = 成功的 tool calls / 总 tool calls

用法: python3 scripts/eval-tool-success-rate.py --since 2026-03-01
输出: 浮点数 0.0-1.0
"""
import argparse, json, os, glob, re

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--since', required=True)
    p.add_argument('--sessions-dir', default=os.path.expanduser('~/.openclaw/agents/main/sessions'))
    return p.parse_args()

def session_date(path):
    fname = os.path.basename(path)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', fname)
    if m:
        return m.group(1)
    try:
        with open(path, 'r') as f:
            first = f.readline().strip()
            if first:
                d = json.loads(first)
                return d.get('timestamp', '')[:10]
    except:
        pass
    return None

ERROR_PATTERNS = re.compile(
    r'"error"|"status":\s*"error"|ENOENT|EACCES|command not found|'
    r'TypeError|SyntaxError|ModuleNotFoundError|FileNotFoundError|'
    r'401 Unauthorized|403 Forbidden|404 Not Found|500 Internal|'
    r'timeout|ETIMEDOUT',
    re.IGNORECASE
)

def analyze_session(path):
    """Returns (total_calls, error_calls)"""
    total = 0
    errors = 0
    last_was_toolcall = False
    
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
                    role = msg.get('role', '')
                    content = msg.get('content', '')
                    
                    if role == 'assistant' and isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'toolCall':
                                total += 1
                                last_was_toolcall = True
                    
                    # Check for tool results in both 'toolResult' role and nested in 'user' content
                    is_tool_result = False
                    if role == 'toolResult':
                        is_tool_result = True
                    elif role == 'user' and isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'toolResult':
                                is_tool_result = True
                                break
                    
                    if is_tool_result and last_was_toolcall:
                        text = json.dumps(content) if isinstance(content, (list, dict)) else str(content)
                        if ERROR_PATTERNS.search(text):
                            errors += 1
                        last_was_toolcall = False
                        
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return total, errors

def main():
    args = parse_args()
    sessions = glob.glob(os.path.join(args.sessions_dir, '*.jsonl'))
    
    total_calls = 0
    total_errors = 0
    
    for path in sessions:
        date = session_date(path)
        if date and date < args.since:
            continue
        calls, errs = analyze_session(path)
        total_calls += calls
        total_errors += errs
    
    if total_calls == 0:
        print("1.0")
    else:
        rate = (total_calls - total_errors) / total_calls
        print(f"{rate:.4f}")

if __name__ == '__main__':
    main()
