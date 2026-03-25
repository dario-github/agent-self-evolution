#!/usr/bin/env python3
"""Agent 能力评估 — M2: 规则触发率

触发定义：session 的 assistant 回复中含拦截器标记（T1-T11, T[n]）的显式引用
触发率 = 含拦截器标记的 session 数 / 总 session 数（排除纯 cron/heartbeat session）

用法: python3 scripts/eval-rule-trigger-rate.py --since 2026-03-01
输出: 浮点数 0.0-1.0
"""
import argparse, json, os, glob, re

RULE_PATTERN = re.compile(r'\bT[0-9]{1,2}\b')  # T1, T2, ..., T11
# Also match explicit rule references in thinking blocks and text
RULE_KEYWORDS = re.compile(
    r'拦截器|不可逆确认|数据严谨|验证深度|异常止损|社媒链接|时效拦截|纠正即落盘|截断防护|搜索语言'
    r'|memory_gate|三步自检|CoT 检查|匹配.*规则.*执行'
    r'|先查数据|先调研|先验证|必须.*工具验证'
    r'|不可逆.*确认|T3.*确认|T6.*严谨|T8.*验证',
    re.IGNORECASE
)

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

def is_interactive_session(path):
    """Filter out pure cron/heartbeat sessions (no user messages from human)."""
    try:
        with open(path, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    if d.get('type') == 'message':
                        msg = d.get('message', {})
                        if msg.get('role') == 'user':
                            content = msg.get('content', '')
                            text = content if isinstance(content, str) else json.dumps(content)
                            # Skip pure heartbeat/cron triggers
                            if 'HEARTBEAT' in text and 'cron' not in text.lower():
                                continue
                            if '[System Message]' in text and 'cron job' in text:
                                continue
                            return True  # Has a real user message
                except:
                    continue
    except:
        pass
    return False

def has_rule_reference(path):
    """Check if any assistant message references interceptor rules.
    Scans both visible text AND thinking blocks."""
    try:
        with open(path, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    if d.get('type') == 'message':
                        msg = d.get('message', {})
                        if msg.get('role') == 'assistant':
                            content = msg.get('content', '')
                            if isinstance(content, list):
                                text = ' '.join(
                                    b.get('text', '') for b in content
                                    if isinstance(b, dict) and b.get('type') in ('text', 'thinking')
                                )
                            else:
                                text = str(content)
                            if RULE_PATTERN.search(text) or RULE_KEYWORDS.search(text):
                                return True
                except:
                    continue
    except:
        pass
    return False

def main():
    args = parse_args()
    sessions = glob.glob(os.path.join(args.sessions_dir, '*.jsonl'))
    
    total_interactive = 0
    total_with_rules = 0
    
    for path in sessions:
        date = session_date(path)
        if date and date < args.since:
            continue
        if not is_interactive_session(path):
            continue
        total_interactive += 1
        if has_rule_reference(path):
            total_with_rules += 1
    
    if total_interactive == 0:
        print("0.0")
    else:
        rate = total_with_rules / total_interactive
        print(f"{rate:.4f}")

if __name__ == '__main__':
    main()
