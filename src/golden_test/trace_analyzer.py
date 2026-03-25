#!/usr/bin/env python3
"""
trace_analyzer.py — Tool Call Trace Parser for Agent Sessions

Parses AI agent session JSONL files, extracting tool calls for use in
evaluation and scoring.

Supports two input modes:
  1. JSONL file path (full session log)
  2. Plain text response (fallback: extracts tool call mentions from text)

JSONL Format (generic agent session log):
  - Each line is a JSON object
  - type="message" + role="assistant" → content contains tool call blocks
  - type="message" + role="toolResult"  → tool return content

Library usage:
    from agent_self_evolution.golden_test.trace_analyzer import TraceAnalyzer

    ta = TraceAnalyzer("/path/to/session.jsonl")
    ta.has_tool_call("exec")            # True/False
    ta.no_tool_call("web_search")       # Prohibition check
    ta.get_tool_args("exec")            # List of all call arguments
    ta.has_tool_before("search", "read")  # Ordering check
    ta.get_final_output()               # Last assistant text block

CLI usage:
    python -m agent_self_evolution.golden_test.trace_analyzer <file> [--tool exec] [--list]
"""

import argparse
import json
import os
import re
import sys
from typing import Optional


# ── Tool aliases ───────────────────────────────────────────────────────────────
# Maps canonical names to their common aliases across different agent frameworks.
# Extend this table to match your agent's tool naming conventions.

TOOL_ALIASES: dict[str, list[str]] = {
    "web_search":   ["web_search", "search", "brave_search"],
    "file_read":    ["read", "file_read", "memory_search", "memory_get"],
    "file_write":   ["write", "edit", "file_write"],
    "exec":         ["exec", "bash", "shell"],
    "message_send": ["message", "send_message"],
    "spawn":        ["sessions_spawn", "spawn_agent"],
}

# Reverse mapping: alias → canonical name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in TOOL_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canonical
    _ALIAS_TO_CANONICAL[_canonical.lower()] = _canonical


def _expand_name(name: str) -> set[str]:
    """Expand a tool name to its full alias set (case-insensitive)."""
    name_lower = name.lower()
    if name_lower in TOOL_ALIASES:
        result = {name_lower} | {a.lower() for a in TOOL_ALIASES[name_lower]}
        return result
    canonical = _ALIAS_TO_CANONICAL.get(name_lower)
    if canonical and canonical in TOOL_ALIASES:
        result = {canonical} | {a.lower() for a in TOOL_ALIASES[canonical]}
        return result
    return {name_lower}


# ── TraceAnalyzer ──────────────────────────────────────────────────────────────

class TraceAnalyzer:
    """
    Parse agent session JSONL and query tool call traces.

    Supports two input modes:
      - JSONL file: parses type="message" + role="assistant" tool call blocks
      - Plain text: extracts tool name mentions (fallback, lower accuracy)
    """

    def __init__(self, session_jsonl_path_or_response_text: str):
        """
        Args:
            session_jsonl_path_or_response_text:
                Path to a JSONL session file, or a plain text response string.
        """
        self._tool_calls: list[dict] = []
        self._text_blocks: list[str] = []
        self._mode: str = "unknown"

        inp = session_jsonl_path_or_response_text
        if self._is_file_path(inp):
            self._parse_jsonl_file(inp)
            self._mode = "jsonl"
        else:
            parsed = self._try_parse_jsonl_text(inp)
            if parsed:
                self._tool_calls, self._text_blocks = parsed
                self._mode = "jsonl_text"
            else:
                self._tool_calls = self._parse_text_mentions(inp)
                self._text_blocks = [inp]
                self._mode = "text"

    # ── Private parsing methods ────────────────────────────────────────────────

    @staticmethod
    def _is_file_path(s: str) -> bool:
        if len(s) > 260 or "\n" in s:
            return False
        return os.path.isfile(s)

    def _parse_jsonl_file(self, path: str) -> None:
        seq = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    self._extract_from_obj(obj, seq_ref=[seq])
                    seq = len(self._tool_calls)
        except OSError as e:
            raise FileNotFoundError(f"Cannot read JSONL file: {path}") from e

    def _try_parse_jsonl_text(self, text: str) -> Optional[tuple]:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return None
        valid = 0
        tool_calls: list[dict] = []
        text_blocks: list[str] = []
        seq_ref = [0]
        for line in lines:
            try:
                obj = json.loads(line)
                if "type" in obj or "message" in obj:
                    valid += 1
                    self._extract_from_obj_into(obj, seq_ref, tool_calls, text_blocks)
            except json.JSONDecodeError:
                pass
        if valid >= 1:
            return tool_calls, text_blocks
        return None

    def _extract_from_obj(self, obj: dict, seq_ref: list) -> None:
        self._extract_from_obj_into(obj, seq_ref, self._tool_calls, self._text_blocks)

    @staticmethod
    def _extract_from_obj_into(
        obj: dict,
        seq_ref: list,
        tool_calls: list,
        text_blocks: list,
    ) -> None:
        msg_type = obj.get("type")

        # Standard JSONL format: type="message"
        if msg_type == "message":
            msg = obj.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])

            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "toolCall":
                        tool_calls.append({
                            "name": block.get("name", ""),
                            "arguments": block.get("arguments", {}),
                            "seq": seq_ref[0],
                            "id": block.get("id", ""),
                        })
                        seq_ref[0] += 1
                    elif btype == "text":
                        t = block.get("text", "").strip()
                        if t:
                            text_blocks.append(t)

        # Legacy format: direct {"role": "assistant", "toolCalls": [...]}
        elif "role" in obj:
            role = obj.get("role", "")
            if role == "assistant":
                for tc in obj.get("toolCalls", []):
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "arguments": tc.get("args", tc.get("arguments", {})),
                        "seq": seq_ref[0],
                        "id": tc.get("id", ""),
                    })
                    seq_ref[0] += 1
                content = obj.get("content", "")
                if isinstance(content, str) and content.strip():
                    text_blocks.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text", "").strip()
                            if t:
                                text_blocks.append(t)

    # ── Public API ─────────────────────────────────────────────────────────────

    def has_tool_call(self, name: str, aliases: list = None) -> bool:
        """Check if the trace contains a call to the specified tool."""
        names = _expand_name(name)
        if aliases:
            for a in aliases:
                names |= _expand_name(a)
        for tc in self._tool_calls:
            if tc["name"].lower() in names:
                return True
        return False

    def no_tool_call(self, name: str) -> bool:
        """Prohibition check: return True if the tool was NOT called."""
        return not self.has_tool_call(name)

    def tool_call_count(self, name: str) -> int:
        """Count how many times a tool was called (including aliases)."""
        names = _expand_name(name)
        return sum(1 for tc in self._tool_calls if tc["name"].lower() in names)

    def get_tool_args(self, name: str) -> list:
        """Return all argument dicts for calls to the specified tool."""
        names = _expand_name(name)
        return [tc["arguments"] for tc in self._tool_calls if tc["name"].lower() in names]

    def has_tool_before(self, first: str, second: str) -> bool:
        """Ordering check: was `first` called before `second`?"""
        first_names = _expand_name(first)
        second_names = _expand_name(second)

        first_seq = next(
            (tc["seq"] for tc in self._tool_calls if tc["name"].lower() in first_names),
            None,
        )
        second_seq = next(
            (tc["seq"] for tc in self._tool_calls if tc["name"].lower() in second_names),
            None,
        )
        if first_seq is None or second_seq is None:
            return False
        return first_seq < second_seq

    def get_all_tool_names(self) -> list:
        """Return all tool names in call order (with duplicates)."""
        return [tc["name"] for tc in self._tool_calls]

    def get_final_output(self) -> str:
        """Return the last assistant text block."""
        if self._text_blocks:
            return self._text_blocks[-1]
        return ""

    def get_all_output(self) -> str:
        """Return all assistant text joined with newlines."""
        return "\n".join(self._text_blocks)

    # ── Text fallback ──────────────────────────────────────────────────────────

    def _parse_text_mentions(self, text: str) -> list:
        """Fallback: extract tool name mentions from plain text (lower accuracy)."""
        pseudo: list[dict] = []
        seq = 0
        text_lower = text.lower()

        all_names: set[str] = set()
        for canonical, aliases in TOOL_ALIASES.items():
            all_names.add(canonical)
            all_names.update(a.lower() for a in aliases)

        found: list[tuple[int, str]] = []
        for name in all_names:
            idx = text_lower.find(name)
            if idx >= 0:
                found.append((idx, name))

        found.sort(key=lambda x: x[0])
        for _, name in found:
            pseudo.append({
                "name": name,
                "arguments": {},
                "seq": seq,
                "id": f"text_mention_{seq}",
            })
            seq += 1

        return pseudo

    # ── Debug / summary ────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Parsing mode: 'jsonl' | 'jsonl_text' | 'text' | 'unknown'."""
        return self._mode

    @property
    def is_reliable(self) -> bool:
        """True when trace data is from a proper JSONL source (not text fallback).

        Note: text mode may misidentify common words (read/write/exec) as tool calls.
        Scoring logic should treat is_reliable=False results as 'inconclusive'.
        """
        return self._mode in ("jsonl", "jsonl_text")

    def summary(self) -> str:
        reliable_tag = "reliable" if self.is_reliable else "UNRELIABLE (text fallback)"
        unique_tools = list(dict.fromkeys(tc["name"] for tc in self._tool_calls))
        return (
            f"[TraceAnalyzer mode={self._mode} {reliable_tag}] "
            f"{len(self._tool_calls)} tool calls, "
            f"{len(self._text_blocks)} text blocks | "
            f"tools: {unique_tools}"
        )


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse agent session JSONL for tool call traces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="JSONL file path or response text file")
    parser.add_argument("--tool", "-t", help="Check if a specific tool was called")
    parser.add_argument("--list", "-l", action="store_true", help="List all tool calls")
    parser.add_argument("--output", "-o", help="Write JSON output to file")
    parser.add_argument("--summary", "-s", action="store_true", help="Print summary")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r") as f:
        content = f.read()

    ta = TraceAnalyzer(content if "\n" in content else args.input)

    if args.summary:
        print(ta.summary())

    if args.list:
        calls = ta.get_all_tool_names()
        if calls:
            print(f"Tool calls ({len(calls)}):")
            for i, name in enumerate(calls):
                print(f"  [{i}] {name}")
        else:
            print("No tool calls found")

    if args.tool:
        found = ta.has_tool_call(args.tool)
        count = ta.tool_call_count(args.tool)
        status = "FOUND" if found else "NOT FOUND"
        print(f"{status}  tool={args.tool}  count={count}")
        if found:
            for i, a in enumerate(ta.get_tool_args(args.tool)):
                print(f"  call[{i}] args: {json.dumps(a, ensure_ascii=False)[:200]}")

    if args.output:
        out = {
            "mode": ta._mode,
            "tool_calls": ta._tool_calls,
            "text_blocks_count": len(ta._text_blocks),
            "final_output_preview": ta.get_final_output()[:200],
        }
        with open(args.output, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Output written to: {args.output}")

    if not (args.summary or args.list or args.tool or args.output):
        print(ta.summary())
        calls = ta.get_all_tool_names()
        if calls:
            print("Tool calls:", calls)


if __name__ == "__main__":
    main()
