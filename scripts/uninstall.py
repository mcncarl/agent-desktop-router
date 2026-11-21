#!/usr/bin/env python3
"""Cleanly uninstalls agent-desktop-router hooks, wrappers, and rule blocks (moves files to macOS Trash)."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

WRAPPER_MARKER = "# ADR_MANAGED_WRAPPER_V1"
RULE_START = "<!-- agent-desktop-router:start -->"
RULE_END = "<!-- agent-desktop-router:end -->"


def move_to_trash(path: Path, trash_dir: Path, dry_run: bool) -> str | None:
    if not path.exists():
        return None
    stamp = int(time.time() * 1000)
    dest = trash_dir / f"{path.name}-adr-{stamp}"
    seq = 1
    while dest.exists():
        dest = trash_dir / f"{path.name}-adr-{stamp}-{seq}"
        seq += 1
    if not dry_run:
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
    return str(dest)


def strip_rule_block(path: Path, dry_run: bool) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"\n*" + re.escape(RULE_START) + r".*?" + re.escape(RULE_END) + r"\n*", re.S)
    if not pattern.search(text):
        return False
    cleaned = pattern.sub("\n", text).rstrip() + ("\n" if text.strip() else "")
    if not dry_run:
        path.write_text(cleaned, encoding="utf-8")
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Uninstall agent-desktop-router.")
    p.add_argument("--home", default=None, help="Override HOME directory")
    p.add_argument("--prefix", default=None, help="Install prefix")
    p.add_argument("--bin-dir", default=None, help="CLI wrapper directory")
    p.add_argument("--purge-config", action="store_true", help="Also move ~/.config/agent-desktop-router to Trash")
    p.add_argument("--dry-run", action="store_true", help="Preview uninstall without modifying files")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else home / ".local" / "share" / "agent-desktop-router"
    bin_dir = Path(args.bin_dir).expanduser().resolve() if args.bin_dir else home / ".local" / "bin"
    trash_dir = home / ".Trash"

    # 1. Remove Claude PreToolUse entry
    claude_settings = home / ".claude" / "settings.json"
    if claude_settings.is_file():
        data = json.loads(claude_settings.read_text(encoding="utf-8"))
        pre = (data.get("hooks") or {}).get("PreToolUse") or []
        kept = [item for item in pre if not ("agent-desktop-router" in json.dumps(item) and "guard.py" in json.dumps(item))]
        if len(kept) != len(pre) and not args.dry_run:
            data["hooks"]["PreToolUse"] = kept
            claude_settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2. Strip managed rule blocks
    strip_rule_block(home / ".codex" / "AGENTS.md", args.dry_run)
    strip_rule_block(home / ".claude" / "CLAUDE.md", args.dry_run)

    # 3. Move managed skills, wrapper, Antigravity plugin, and runtime prefix to Trash
    wrapper = bin_dir / "agent-desktop"
    if wrapper.is_file() and WRAPPER_MARKER in wrapper.read_text(encoding="utf-8", errors="ignore"):
        move_to_trash(wrapper, trash_dir, args.dry_run)

    for p in (
        home / ".claude" / "skills" / "desktop-browser-routing",
        home / ".codex" / "skills" / "desktop-browser-routing",
        home / ".gemini" / "config" / "plugins" / "agent-desktop-router",
        prefix,
    ):
        move_to_trash(p, trash_dir, args.dry_run)

    if args.purge_config:
        move_to_trash(home / ".config" / "agent-desktop-router", trash_dir, args.dry_run)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}agent-desktop-router uninstalled cleanly (removed files moved to {trash_dir}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
