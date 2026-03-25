#!/usr/bin/env python3
"""
ablation-gen.py — 消融文件生成器

给定一个消融组 ID (AG1-AG6)，读取原始 workspace 文件，
移除对应 section，输出修改后的文件集到指定目录。

用法：
  python3 scripts/ablation-gen.py --group AG1 --output /tmp/ablation/AG1/
  python3 scripts/ablation-gen.py --group AG0 --output /tmp/ablation/AG0/  # control (copy)
  python3 scripts/ablation-gen.py --verify /tmp/ablation/AG1/  # 验证消融完整性

方法：基于 section 边界标记（H2 heading / 已知 pattern）定位并移除目标段落。
局限：依赖文件结构稳定性，heading 变更需同步更新 section 定义。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

WORKSPACE = Path(os.path.expanduser("."))
WORKSPACE_FILES = [
    "AGENTS.md", "SOUL.md", "TOOLS.md",
    "USER.md", "IDENTITY.md", "MEMORY.md", "HEARTBEAT.md"
]

# ─── Section definitions ───
# Each section: (file, start_pattern, end_pattern_or_next_section)
# start_pattern: regex matching the section heading line
# end_pattern: regex matching the NEXT section's heading (exclusive), or None for EOF

ABLATION_GROUPS = {
    "AG1": {
        "name": "安全规则核心",
        "sections": {
            "agents.t3": ("AGENTS.md", r"^\*\*T3 不可逆确认\*\*", r"^\*\*T3 补充"),
            "agents.t3_supplement": ("AGENTS.md", r"^\*\*T3 补充", r"^\*\*T6 数据严谨性\*\*"),
            "agents.t6": ("AGENTS.md", r"^\*\*T6 数据严谨性\*\*", r"^\*\*T6 补充"),
            "agents.t6_supplement": ("AGENTS.md", r"^\*\*T6 补充", r"^\*\*T6a 投资数据"),
            "agents.t6a": ("AGENTS.md", r"^\*\*T6a 投资数据", r"^\*\*T7 状态变更"),
            "agents.t8": ("AGENTS.md", r"^\*\*T8 验证深度\*\*", r"^\*\*T9 纠正即改"),
            "agents.t14": ("AGENTS.md", r"^\*\*T14 内容安全", r"^\*\*T15 权威声称"),
            "agents.t15": ("AGENTS.md", r"^\*\*T15 权威声称", r"^## CoT 检查协议"),
            "agents.cot": ("AGENTS.md", r"^## CoT 检查协议", r"^## 拦截器"),
        }
    },
    "AG2": {
        "name": "安全规则辅助",
        "sections": {
            "agents.t1": ("AGENTS.md", r"^\*\*T1 时效拦截\*\*", r"^\*\*T4 异常止损"),
            "agents.t4": ("AGENTS.md", r"^\*\*T4 异常止损\*\*", r"^\*\*T5 社媒链接"),
            "agents.t7": ("AGENTS.md", r"^\*\*T7 状态变更回写\*\*", r"^\*\*T8 验证深度"),
            "agents.t9": ("AGENTS.md", r"^\*\*T9 纠正即改源头", r"^\*\*T9 触发句式"),
            "agents.t9_patterns": ("AGENTS.md", r"^\*\*T9 触发句式", r"^\*\*T10 大文件"),
            "agents.t10": ("AGENTS.md", r"^\*\*T10 大文件截断", r"^\*\*T11 搜索语言"),
            "agents.t11": ("AGENTS.md", r"^\*\*T11 搜索语言参数", r"^\*\*T12 Discord"),
            "agents.t13": ("AGENTS.md", r"^\*\*T13 重大决策即时落盘", r"^\*\*T14 内容安全"),
            "agents.t16": ("AGENTS.md", r"^\*\*T16 默认反问", r"^## 安全拦截日志"),
            "agents.security_log": ("AGENTS.md", r"^## 安全拦截日志协议", r"^## Announce 处理"),
        }
    },
    "AG3": {
        "name": "SOUL 行为塑造",
        "sections": {
            "soul.core_truths": ("SOUL.md", r"^## Core Truths", r"^## Output Discipline"),
            "soul.output_discipline": ("SOUL.md", r"^## Output Discipline", r"^## Three Modes"),
            "soul.three_modes": ("SOUL.md", r"^## Three Modes", r"^## Core Lessons"),
            "soul.intellectual_courage": ("SOUL.md", r"^## Intellectual Courage", r"^## 对owner"),
            "soul.critical_mode": ("SOUL.md", r"^## 对owner", r"^## Thought Partner"),
            "soul.thought_partner": ("SOUL.md", r"^## Thought Partner", r"^## 语言"),
            "soul.iss": ("SOUL.md", r"^## Inner State", r"^## Full Name"),
        }
    },
    "AG4": {
        "name": "工具索引",
        "sections": {
            "tools.model_matrix": ("TOOLS.md", r"^## 模型矩阵", r"^---\n\n## 能力索引|^## 工作规范"),
            "tools.skill_index": ("TOOLS.md", r"^## 能力索引", r"^---\n\n## 文件传输|^## 文件传输"),
            "tools.search_routing": ("TOOLS.md", r"^## 搜索路由", r"^## 浏览器双轨制"),
            "tools.browser_dual": ("TOOLS.md", r"^## 浏览器双轨制", r"^## 通用原则"),
            "tools.xhs": ("TOOLS.md", r"^## 小红书帖子处理", r"^## Discord 频道映射|^## Discord 输出规则"),
            "tools.file_transfer": ("TOOLS.md", r"^## 文件传输", r"^## 关键配置"),
            "tools.openspec": ("TOOLS.md", r"^## 工作规范：OpenSpec", r"^---\n\n## 能力索引|^## 能力索引"),
        }
    },
    "AG5": {
        "name": "记忆/用户档案",
        "sections": {
            "memory.active_context": ("MEMORY.md", r"^## Active Context", r"^## Historical"),
            "agents.memory_system": ("AGENTS.md", r"^## 记忆系统", r"^## 工作方法"),
            "user.profile": ("USER.md", None, None),  # entire file
            "identity.self": ("IDENTITY.md", None, None),  # entire file
            "tools.portfolio_isolation": ("TOOLS.md", r"^## 持仓隔离规则", r"^## Slack vs Discord|^## Discord 频道映射"),
            "tools.slack_channels": ("TOOLS.md", r"^## Slack 频道映射", r"^## 持仓隔离规则|^## Discord 频道映射"),
            "tools.discord_channels": ("TOOLS.md", r"^## Discord 频道映射", r"^## Discord 输出规则"),
        }
    },
    "AG6": {
        "name": "流程协议",
        "sections": {
            "agents.bootstrap": ("AGENTS.md", r"^## Session Bootstrap", r"^## 铁律"),
            "agents.announce": ("AGENTS.md", r"^## Announce 处理协议", r"^## 能力菜单"),
            "agents.trigger_table": ("AGENTS.md", r"^## 情境规则触发表", r"^## 记忆系统"),
            "agents.iteration": ("AGENTS.md", r"^## 自动迭代原则", r"^## 输出"),
            "agents.swarm": ("AGENTS.md", r"^### Swarm 蜂群阵容", r"^> 完整模型矩阵"),
        }
    },
}


def sha256(filepath: Path) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def remove_section(content: str, start_pat: str, end_pat: str | None) -> str:
    """Remove a section from content between start_pat and end_pat.
    
    start_pat: regex for the section start line (inclusive)
    end_pat: regex for the next section start line (exclusive), or None for EOF
    """
    lines = content.split("\n")
    result = []
    in_removal = False
    
    for line in lines:
        if not in_removal and re.match(start_pat, line):
            in_removal = True
            continue
        
        if in_removal:
            if end_pat:
                # Check multiple patterns (pipe-separated alternatives)
                for pat in end_pat.split("|"):
                    if re.match(pat.strip(), line):
                        in_removal = False
                        result.append(line)
                        break
                if in_removal:
                    continue  # still removing
            else:
                continue  # remove till EOF
        else:
            result.append(line)
    
    return "\n".join(result)


def generate_ablation(group_id: str, output_dir: Path):
    """Generate ablated workspace files for a given group."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if group_id == "AG0":
        # Control: just copy everything
        for fname in WORKSPACE_FILES:
            src = WORKSPACE / fname
            if src.exists():
                shutil.copy2(src, output_dir / fname)
        print(f"✅ AG0 (Control): copied {len(WORKSPACE_FILES)} files to {output_dir}")
        return
    
    group = ABLATION_GROUPS.get(group_id)
    if not group:
        print(f"❌ Unknown group: {group_id}")
        return
    
    # Read all workspace files
    file_contents = {}
    for fname in WORKSPACE_FILES:
        src = WORKSPACE / fname
        if src.exists():
            file_contents[fname] = src.read_text(encoding="utf-8")
        else:
            file_contents[fname] = ""
    
    # Apply ablations
    removed_sections = []
    for section_id, (fname, start_pat, end_pat) in group["sections"].items():
        if start_pat is None and end_pat is None:
            # Remove entire file content (replace with minimal placeholder)
            file_contents[fname] = f"<!-- ablated: {section_id} -->\n"
            removed_sections.append(section_id)
        else:
            original = file_contents[fname]
            ablated = remove_section(original, start_pat, end_pat)
            if len(ablated) < len(original):
                removed_sections.append(section_id)
                file_contents[fname] = ablated
            else:
                print(f"  ⚠️ Section {section_id} not found in {fname} (pattern: {start_pat})")
    
    # Write ablated files
    for fname in WORKSPACE_FILES:
        (output_dir / fname).write_text(file_contents[fname], encoding="utf-8")
    
    # Write manifest
    manifest = {
        "group_id": group_id,
        "group_name": group["name"],
        "removed_sections": removed_sections,
        "total_sections_in_group": len(group["sections"]),
        "files_modified": list(set(f for f, _, _ in group["sections"].values())),
        "checksums": {
            fname: sha256(output_dir / fname)
            for fname in WORKSPACE_FILES
            if (output_dir / fname).exists()
        }
    }
    
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    
    found = len(removed_sections)
    total = len(group["sections"])
    status = "✅" if found == total else "⚠️"
    print(f"{status} {group_id} ({group['name']}): removed {found}/{total} sections → {output_dir}")
    if found < total:
        missing = set(group["sections"].keys()) - set(removed_sections)
        print(f"  Missing: {missing}")


def verify_ablation(ablation_dir: Path):
    """Verify ablation integrity against manifest."""
    manifest_path = ablation_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"❌ No manifest.json in {ablation_dir}")
        return False
    
    manifest = json.loads(manifest_path.read_text())
    
    print(f"Verifying {manifest['group_id']} ({manifest['group_name']})...")
    
    all_ok = True
    for fname, expected_hash in manifest["checksums"].items():
        fpath = ablation_dir / fname
        if not fpath.exists():
            print(f"  ❌ Missing: {fname}")
            all_ok = False
            continue
        actual_hash = sha256(fpath)
        if actual_hash != expected_hash:
            print(f"  ❌ Hash mismatch: {fname}")
            all_ok = False
        else:
            print(f"  ✅ {fname}")
    
    # Check that ablated sections are actually missing
    group = ABLATION_GROUPS.get(manifest["group_id"], {})
    for section_id in manifest["removed_sections"]:
        sec_def = group.get("sections", {}).get(section_id)
        if sec_def:
            fname, start_pat, end_pat = sec_def
            if start_pat:
                content = (ablation_dir / fname).read_text()
                if re.search(start_pat, content, re.MULTILINE):
                    print(f"  ❌ Section {section_id} still present in {fname}!")
                    all_ok = False
                else:
                    print(f"  ✅ Section {section_id} confirmed removed")
    
    status = "✅ PASS" if all_ok else "❌ FAIL"
    print(f"\n{status}: {manifest['group_id']}")
    return all_ok


def generate_all():
    """Generate all ablation groups."""
    base_dir = Path("/tmp/ablation")
    
    # Control
    generate_ablation("AG0", base_dir / "AG0")
    
    # Ablation groups
    for gid in ["AG1", "AG2", "AG3", "AG4", "AG5", "AG6"]:
        generate_ablation(gid, base_dir / gid)
    
    # Generate original checksums for restore verification
    original_checksums = {
        fname: sha256(WORKSPACE / fname)
        for fname in WORKSPACE_FILES
        if (WORKSPACE / fname).exists()
    }
    
    checksums_path = base_dir / "original-checksums.json"
    checksums_path.write_text(json.dumps(original_checksums, indent=2))
    print(f"\n✅ Original checksums saved to {checksums_path}")
    print(f"✅ All ablation groups generated in {base_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ablation file generator")
    parser.add_argument("--group", help="Generate specific group (AG0-AG6)")
    parser.add_argument("--output", help="Output directory", type=Path)
    parser.add_argument("--verify", help="Verify ablation directory", type=Path)
    parser.add_argument("--all", action="store_true", help="Generate all groups")
    
    args = parser.parse_args()
    
    if args.verify:
        verify_ablation(args.verify)
    elif args.all:
        generate_all()
    elif args.group and args.output:
        generate_ablation(args.group, args.output)
    else:
        parser.print_help()
