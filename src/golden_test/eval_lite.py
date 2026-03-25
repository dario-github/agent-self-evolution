#!/usr/bin/env python3
"""
eval_lite.py — Lightweight Evaluation Runner

Static analysis and fast heuristic evaluation for CI integration.
"""

import json
import re

def eval_mr(test, response_text):
    """Evaluate Memory Retrieval by keyword matching."""
    keywords = test.get('expected_keywords', [])
    match_min = test.get('match_min', 1)
    matches = sum(1 for kw in keywords if kw.lower() in response_text.lower())
    passed = matches >= match_min
    score = min(5, round(5 * matches / max(len(keywords), 1)))
    return {
        'id': test.get('id', ''),
        'passed': passed,
        'score': score,
        'method': 'keyword_match'
    }

def eval_tu(test, session_log):
    """Evaluate Tool Usage by checking tool log patterns."""
    expected_pattern = test.get('expected_pattern', '')
    wrong_tools = test.get('wrong_tools', [])

    tool_calls = []
    for entry in session_log:
        msg = entry.get('message', {})
        if msg.get('role') == 'assistant':
            for tc in (msg.get('tool_calls') or []):
                fn = tc.get('function', {}).get('name', '')
                tool_calls.append(f"{fn}")

    calls_text = ' '.join(tool_calls)
    found_expected = bool(re.search(expected_pattern, calls_text, re.I)) if expected_pattern else True
    used_wrong = [w for w in wrong_tools if w in calls_text]

    passed = found_expected and not used_wrong
    score = 5 if passed else (2 if found_expected else 1)

    return {
        'id': test.get('id', ''),
        'passed': passed,
        'score': score,
        'method': 'tool_log_check'
    }
