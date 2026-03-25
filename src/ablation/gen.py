#!/usr/bin/env python3
"""
ablation-gen.py — Ablation file generator

Given an ablation group ID, reads original workspace files,
removes the corresponding sections, and outputs modified files.

Usage:
  python3 scripts/ablation-gen.py --group AG1 --output /tmp/ablation/AG1/
  python3 scripts/ablation-gen.py --group AG0 --output /tmp/ablation/AG0/  # control (copy)
  python3 scripts/ablation-gen.py --verify /tmp/ablation/AG1/
  python3 scripts/ablation-gen.py --all

Section definitions are loaded from ablation_config.yaml (not checked into git).
See ablation_config.example.yaml for the format.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import yaml

WORKSPACE = Path(os.path.expanduser("."))

# ─── Config loading ───

_CONFIG = None

def _find_config() -> Path:
    """Find ablation_config.yaml in workspace or env."""
    env_path = os.environ.get("ABLATION_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    candidates = [
        WORKSPACE / "ablation_config.yaml",
        WORKSPACE / "config" / "ablation_config.yaml",
        Path.home() / ".config" / "agent-self-evolution" / "ablation_config.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        "ablation_config.yaml not found. "
        "Copy ablation_config.example.yaml to ablation_config.yaml and customize it. "
        "Or set ABLATION_CONFIG=/path/to/config.yaml"
    )


def load_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        config_path = _find_config()
        with open(config_path) as f:
            _CONFIG = yaml.safe_load(f)
        print(f"  📋 Config loaded from: {config_path}")
    return _CONFIG


def get_workspace_files() -> list[str]:
    return load_config().get("workspace_files", [
        "AGENTS.md", "SOUL.md", "TOOLS.md",
        "USER.md", "IDENTITY.md", "MEMORY.md", "HEARTBEAT.md"
    ])


def get_ablation_groups() -> dict:
    """Parse config groups into the internal format."""
    raw = load_config().get("groups", {})
    groups = {}
    for gid, gdef in raw.items():
        sections = {}
        for sid, sdef in gdef.get("sections", {}).items():
            start = sdef.get("start")
            end = sdef.get("end")
            sections[sid] = (sdef["file"], start, end)
        groups[gid] = {
            "name": gdef.get("name", gid),
            "sections": sections,
        }
    return groups


# ─── Core logic ───

def sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def remove_section(content: str, start_pat: str, end_pat: str | None) -> str:
    """Remove a section from content between start_pat and end_pat."""
    lines = content.split("\n")
    result = []
    in_removal = False

    for line in lines:
        if not in_removal and re.match(start_pat, line):
            in_removal = True
            continue

        if in_removal:
            if end_pat:
                for pat in end_pat.split("|"):
                    if re.match(pat.strip(), line):
                        in_removal = False
                        result.append(line)
                        break
                if in_removal:
                    continue
            else:
                continue
        else:
            result.append(line)

    return "\n".join(result)


def generate_ablation(group_id: str, output_dir: Path):
    """Generate ablated workspace files for a given group."""
    workspace_files = get_workspace_files()
    output_dir.mkdir(parents=True, exist_ok=True)

    if group_id == "AG0":
        for fname in workspace_files:
            src = WORKSPACE / fname
            if src.exists():
                shutil.copy2(src, output_dir / fname)
        print(f"✅ AG0 (Control): copied {len(workspace_files)} files to {output_dir}")
        return

    groups = get_ablation_groups()
    group = groups.get(group_id)
    if not group:
        available = ", ".join(sorted(groups.keys()))
        print(f"❌ Unknown group: {group_id}. Available: {available}")
        return

    file_contents = {}
    for fname in workspace_files:
        src = WORKSPACE / fname
        if src.exists():
            file_contents[fname] = src.read_text(encoding="utf-8")
        else:
            file_contents[fname] = ""

    removed_sections = []
    for section_id, (fname, start_pat, end_pat) in group["sections"].items():
        if start_pat is None and end_pat is None:
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

    for fname in workspace_files:
        (output_dir / fname).write_text(file_contents[fname], encoding="utf-8")

    manifest = {
        "group_id": group_id,
        "group_name": group["name"],
        "removed_sections": removed_sections,
        "total_sections_in_group": len(group["sections"]),
        "files_modified": list(set(f for f, _, _ in group["sections"].values())),
        "checksums": {
            fname: sha256(output_dir / fname)
            for fname in workspace_files
            if (output_dir / fname).exists()
        }
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

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

    groups = get_ablation_groups()
    group = groups.get(manifest["group_id"], {})
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
    groups = get_ablation_groups()
    base_dir = Path("/tmp/ablation")
    workspace_files = get_workspace_files()

    generate_ablation("AG0", base_dir / "AG0")
    for gid in sorted(groups.keys()):
        generate_ablation(gid, base_dir / gid)

    original_checksums = {
        fname: sha256(WORKSPACE / fname)
        for fname in workspace_files
        if (WORKSPACE / fname).exists()
    }
    checksums_path = base_dir / "original-checksums.json"
    checksums_path.write_text(json.dumps(original_checksums, indent=2))
    print(f"\n✅ Original checksums saved to {checksums_path}")
    print(f"✅ All ablation groups generated in {base_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ablation file generator")
    parser.add_argument("--group", help="Generate specific group (AG0, AG1, ...)")
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
