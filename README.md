# Agent Self-Evolution System

**Automated evaluation, ablation testing, and continuous improvement framework for AI agents.**

> 🧪 Golden Test v3: **29/30 passing** (avg 8.5/10) | 5-dimensional evaluation | Ablation experiments with controlled conditions

---

## Motivation

AI agents degrade silently. Without systematic evaluation, you can't tell if a prompt change improved safety or broke tool routing. This system provides:

1. **Golden Test Set** — Curated test cases with known-good answers, scored by strong models
2. **Multi-dimensional Evaluators** — Automated daily metrics across 5 capability axes
3. **Ablation Framework** — Controlled experiments to measure the impact of individual components
4. **Continuous Feedback** — Corrections analysis → root cause → source fix loop

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Evaluation Pipeline                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ Golden   │  │ 5-Dim    │  │  Ablation    │  │
│  │ Test Set │  │ Eval     │  │  Framework   │  │
│  │ (30 Q)   │  │ (daily)  │  │  (on-demand) │  │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘  │
│       ▼              ▼               ▼           │
│  ┌───────────────────────────────────────────┐  │
│  │           Composite Loss Function          │  │
│  │    L = w₁·GT + w₂·eval + w₃·shadow       │  │
│  └───────────────────────────────────────────┘  │
│                      ▼                           │
│  ┌───────────────────────────────────────────┐  │
│  │     Decision: Ship / Rollback / Iterate    │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## Components

### Golden Test Set (`src/golden_test/`)

| Module | Description |
|--------|-------------|
| `runner.py` | Execute test cases against the agent, capture traces |
| `scorer.py` | Score responses using a stronger judge model (GPT-5.4 / Opus) |
| `splitter.py` | Train/val/test split with stratified sampling |
| `trace_analyzer.py` | Analyze execution traces for tool usage patterns |
| `eval_lite.py` | Lightweight evaluation for quick iteration |

### 5-Dimensional Evaluators (`src/evaluators/`)

| Evaluator | What it measures |
|-----------|-----------------|
| `task_completion.py` | Did the agent complete what was asked? |
| `tool_success_rate.py` | Are tool calls succeeding or failing silently? |
| `rule_trigger_rate.py` | Are safety rules firing when they should? |
| `memory_hit_rate.py` | Is memory retrieval returning useful results? |
| `cron_silence.py` | Are scheduled tasks running or failing silently? |

### Ablation Framework (`src/ablation/`)

| Module | Description |
|--------|-------------|
| `gen.py` | Generate ablation conditions (remove component X, measure impact) |
| `runner.py` | Execute ablation experiments with controlled conditions |
| `judge.py` | Score ablated vs. baseline responses |
| `analysis.py` | Statistical analysis of ablation results |
| `orchestrator.py` | End-to-end ablation pipeline orchestration |
| `probe_runner.py` | Targeted probes for specific capability dimensions |
| `eval_batch.py` | Batch evaluation across multiple conditions |

## Key Principles

Borrowed from ML training methodology:

1. **Train/Val/Test Split** — 60/20/20, stratified, seed-fixed. Tune on train, validate on val, milestone on test
2. **Strong Model Judges** — GPT-5.4 / Opus score the agent; the agent never scores itself
3. **Loss-Driven Iteration** — Composite L must decrease; L↑ = rollback, not hotfix
4. **Overfitting Prevention** — Rule complexity penalty; category variance monitoring
5. **Curriculum Learning** — Easy → hard; previous phase metrics must not regress

## Usage

```bash
# Run golden test evaluation
python -m src.golden_test.runner --split val --output results/

# Score results with judge model
python -m src.golden_test.scorer --input results/ --judge openai/gpt-5.4

# Run ablation experiment
python -m src.ablation.orchestrator \
  --condition "remove_soul_md" \
  --baseline results/baseline/ \
  --output results/ablation/

# Daily evaluation (typically run via cron)
python -m src.evaluators.task_completion --date today
python -m src.evaluators.tool_success_rate --date today
```

## Key Findings

From ablation experiments on a production agent system:

- **SOUL.md removal** (7% of total context): Catastrophic degradation — personality collapse, safety rule bypass. This small file is a load-bearing wall
- **Memory system removal**: Graceful degradation on simple tasks, severe on multi-session continuity
- **Rule file removal**: Predictable per-rule impact; some rules compensated by model's built-in alignment

## License

Apache License 2.0 — See [LICENSE](LICENSE).

## Companion Projects

- [**Nous Safety**](https://github.com/dario-github/nous) — Ontology-driven runtime safety engine with Datalog reasoning
- [**Biomorphic Memory**](https://github.com/dario-github/biomorphic-memory) — Brain-inspired agent memory with spreading activation (LongMemEval SOTA 89.8%)

## Install via ClawdHub

```bash
openclaw skills install agent-self-evolution
```

