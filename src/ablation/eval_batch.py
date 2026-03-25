#!/usr/bin/env python3
"""
ablation-eval-batch.py — 单条件批量评估脚本

读取指定条件的 workspace 文件，对每道题：
1. 构建 system prompt（从消融后的 workspace 文件拼接）
2. 直接调 LLM API 获取回答（不走 OpenClaw session）
3. Auto-judge 或标记需要 LLM judge
4. 输出 JSONL 结果

用法：
  python3 scripts/ablation-eval-batch.py --condition AG0 --run 0 --test-dir memory/evaluation/ablation/gt-v4-tests
  
原理：消融实验不需要完整的 OpenClaw session（tool calls 等），
因为我们测的是「context injection 对回答质量的影响」而非「工具使用能力」。
对于 MR/RC/MS 类题目，纯 LLM 补全即可评估。TU 类需要特殊处理。
"""

import argparse
import json
import os
import re
import sys
import time
import yaml
import hashlib
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("ABLATION_WORKSPACE", os.path.expanduser(".")))
ABLATION_DIR = Path(os.environ.get("ABLATION_DIR", "/tmp/ablation"))
RESULTS_DIR = WORKSPACE / "memory" / "evaluation" / "ablation" / "results"

WORKSPACE_FILES = [
    "AGENTS.md", "SOUL.md", "TOOLS.md",
    "USER.md", "IDENTITY.md", "MEMORY.md"
]

# === Provider config (from openclaw.json proxy setup) ===
def _load_provider_config():
    """Load provider config from OpenClaw config."""
    cfg_path = Path(os.path.expanduser("~/.openclaw/openclaw.json"))
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        c = json.load(f)
    return c.get("models", {}).get("providers", {})

_PROVIDERS = None
def get_providers():
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = _load_provider_config()
    return _PROVIDERS


def call_llm(system_prompt: str, user_prompt: str, model: str = "anthropic/claude-sonnet-4-6", temperature: float = 0.0) -> str:
    """Call LLM via provider SDK, using OpenClaw proxy config."""
    try:
        providers = get_providers()
        
        if model.startswith("anthropic/"):
            import anthropic
            pcfg = providers.get("anthropic", {})
            base_url = pcfg.get("baseUrl", "https://api.anthropic.com")
            api_key = pcfg.get("apiKey", os.environ.get("ANTHROPIC_API_KEY", ""))
            headers = pcfg.get("headers", {})
            
            client = anthropic.Anthropic(
                api_key=api_key,
                base_url=base_url,
                default_headers=headers,
            )
            model_id = model.replace("anthropic/", "")
            response = client.messages.create(
                model=model_id,
                max_tokens=2000,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        
        elif model.startswith("openai/") or model.startswith("google/") or model.startswith("moonshot/") or model.startswith("deepseek/"):
            import openai
            # All non-anthropic providers use openai-compatible API via proxy
            provider_name = model.split("/")[0]
            pcfg = providers.get(provider_name, providers.get("openai", {}))
            base_url = pcfg.get("baseUrl", "https://api.openai.com/v1")
            api_key = pcfg.get("apiKey", os.environ.get("OPENAI_API_KEY", ""))
            # Resolve env var references
            if api_key.startswith("${") and api_key.endswith("}"):
                env_var = api_key[2:-1]
                api_key = os.environ.get(env_var, "")
            
            client = openai.OpenAI(base_url=base_url, api_key=api_key)
            model_id = model.split("/", 1)[1]
            response = client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content
        
        else:
            return f"ERROR: unsupported model prefix: {model}"
    
    except Exception as e:
        return f"ERROR: {str(e)}"


def build_system_prompt(condition: str) -> str:
    """Build system prompt from ablated workspace files."""
    cond_dir = ABLATION_DIR / condition
    parts = []
    for fname in WORKSPACE_FILES:
        fpath = cond_dir / fname
        if fpath.exists():
            content = fpath.read_text()
            if content.strip():
                parts.append(f"## {fname}\n{content}")
    
    return "You are a personal AI assistant. Below are your workspace configuration files.\n\n" + "\n\n---\n\n".join(parts)


def load_all_tests(test_dir: Path) -> list:
    """Load all test items from YAML files."""
    tests = []
    for fname in sorted(test_dir.glob("all-*.yaml")):
        with open(fname) as f:
            items = yaml.safe_load(f)
            if items:
                tests.extend(items)
    return tests


def judge_auto(test_item: dict, response: str) -> dict | None:
    """Auto-judge: keyword matching for MR, basic checks for others."""
    category = test_item.get("category", "")
    
    if category == "memory_retrieval":
        keywords = test_item.get("expected_keywords", [])
        match_min = test_item.get("match_min", 1)
        matches = sum(1 for kw in keywords if kw.lower() in response.lower())
        
        if matches >= match_min:
            score = 4
        elif matches >= max(1, int(match_min * 0.75)):
            score = 3
        elif matches >= max(1, int(match_min * 0.5)):
            score = 2
        elif matches > 0:
            score = 1
        else:
            score = 0
        
        return {
            "judge_type": "auto_keyword",
            "score": score,
            "matched": matches,
            "total_keywords": len(keywords),
            "match_min": match_min,
        }
    
    # Tool usage tests — check if response mentions correct tools/approaches
    if category == "tool_usage":
        expected_tools = test_item.get("expected_tools", [])
        if expected_tools:
            matches = sum(1 for t in expected_tools if t.lower() in response.lower())
            score = min(4, int(4 * matches / max(1, len(expected_tools))))
            return {
                "judge_type": "auto_tool_mention",
                "score": score,
                "matched": matches,
                "total": len(expected_tools),
            }
    
    return None  # needs LLM judge


def judge_llm_single(test_item: dict, response: str, judge_model: str) -> dict:
    """Single LLM judge."""
    rubric = test_item.get("passing_criteria", test_item.get("expected_behavior", ""))
    prompt_text = test_item.get("prompt", "")
    
    judge_prompt = f"""Score this AI agent response on a 0-4 scale.

TEST PROMPT: {prompt_text}

EXPECTED BEHAVIOR: {rubric}

ACTUAL RESPONSE: {response[:3000]}

RUBRIC:
4=Excellent (fully meets criteria)
3=Good (meets core criteria, minor issues)
2=Partial (significant gaps)
1=Poor (attempts but fails)
0=Fail (doesn't address or violates)

Reply with ONLY valid JSON: {{"score": <0-4>, "reason": "<15 words max>"}}"""

    out = call_llm("You are an evaluation judge. Output only JSON.", judge_prompt, model=judge_model, temperature=0.0)
    
    try:
        match = re.search(r'\{[^}]+\}', out)
        if match:
            parsed = json.loads(match.group())
            return {
                "judge_model": judge_model,
                "score": int(parsed.get("score", 0)),
                "reason": parsed.get("reason", ""),
            }
    except:
        pass
    
    return {"judge_model": judge_model, "score": -1, "reason": "parse_error"}


def run_batch(condition: str, run_idx: int, tests: list, output_dir: Path, judge_models: list = None):
    """Run all tests for one condition+run."""
    if judge_models is None:
        judge_models = ["openai/gpt-5.4"]  # single strong judge for efficiency
    
    output_dir.mkdir(parents=True, exist_ok=True)
    outfile = output_dir / f"{condition}_r{run_idx}.jsonl"
    
    # Check for existing results (resume support)
    done_ids = set()
    if outfile.exists():
        for line in outfile.read_text().strip().split("\n"):
            if line.strip():
                try:
                    r = json.loads(line)
                    done_ids.add(r["test_id"])
                except:
                    pass
    
    system_prompt = build_system_prompt(condition)
    sys_tokens_est = len(system_prompt) // 4
    
    print(f"  {condition} r{run_idx}: {len(tests)} tests, {len(done_ids)} already done, ~{sys_tokens_est} sys tokens")
    
    with open(outfile, "a") as fout:
        for i, test in enumerate(tests):
            test_id = test["id"]
            if test_id in done_ids:
                continue
            
            # Get LLM response
            user_prompt = test["prompt"].strip()
            t0 = time.time()
            response = call_llm(system_prompt, user_prompt)
            elapsed = time.time() - t0
            
            # Judge
            auto = judge_auto(test, response)
            if auto and auto["score"] >= 0:
                score = auto["score"]
                judge_info = auto
            else:
                # LLM judge(s)
                judgments = [judge_llm_single(test, response, m) for m in judge_models]
                valid_scores = [j["score"] for j in judgments if j["score"] >= 0]
                if valid_scores:
                    from collections import Counter
                    c = Counter(valid_scores)
                    score = c.most_common(1)[0][0]
                else:
                    score = 0
                judge_info = {"judge_type": "llm", "judgments": judgments}
            
            result = {
                "test_id": test_id,
                "condition": condition,
                "run_idx": run_idx,
                "category": test.get("category", ""),
                "difficulty": test.get("difficulty", ""),
                "score": score,
                "judge": judge_info,
                "response_len": len(response),
                "elapsed_s": round(elapsed, 1),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            
            fout.write(json.dumps(result, ensure_ascii=False) + "\n")
            fout.flush()
            
            status = "✅" if score >= 3 else "⚠️" if score >= 1 else "❌"
            pct = (i + 1 - len(done_ids)) / (len(tests) - len(done_ids)) * 100 if (len(tests) - len(done_ids)) > 0 else 100
            print(f"    {status} {test_id}: {score}/4 ({elapsed:.1f}s) [{pct:.0f}%]")
    
    # Summary
    results = []
    for line in outfile.read_text().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip malformed lines
    
    scores = [r["score"] for r in results]
    avg = sum(scores) / len(scores) if scores else 0
    total = sum(scores)
    print(f"  📊 {condition} r{run_idx}: {total}/{len(scores)*4} (avg {avg:.2f})")
    
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", required=True, help="Condition (AG0-AG6)")
    parser.add_argument("--run", type=int, required=True, help="Run index (0-2)")
    parser.add_argument("--test-dir", default=str(WORKSPACE / "memory/evaluation/ablation/gt-v4-tests"))
    parser.add_argument("--output-dir", default=str(RESULTS_DIR))
    parser.add_argument("--judge-models", nargs="+", default=["openai/gpt-5.4"])
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tests (0=all)")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6", help="Test subject model")
    
    args = parser.parse_args()
    
    global TEST_MODEL  # not great but simple
    
    tests = load_all_tests(Path(args.test_dir))
    if args.limit > 0:
        tests = tests[:args.limit]
    
    print(f"🔬 Ablation eval: {args.condition} run {args.run}, {len(tests)} tests")
    print(f"   Subject: {args.model}, Judge: {args.judge_models}")
    
    run_batch(
        condition=args.condition,
        run_idx=args.run,
        tests=tests,
        output_dir=Path(args.output_dir),
        judge_models=args.judge_models,
    )


if __name__ == "__main__":
    main()
