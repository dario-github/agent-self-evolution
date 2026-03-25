"""
Golden Test Set — Regression baseline for AI agent behavioral evaluation.

Implements tri-layer scoring:
    L1 Trace Check     — Tool call behavior verification via session JSONL
    L2 Output Check    — Keyword / value matching on final response
    L3 LLM Judge       — Semantic quality evaluation (≤20% of tests)
"""
