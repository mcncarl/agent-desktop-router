#!/usr/bin/env python3
"""Idempotent installer for agent-desktop-router across Claude Code, Codex, and Antigravity."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER_MARKER = "# ADR_MANAGED_WRAPPER_V1"
RULE_START = "<!-- agent-desktop-router:start -->"
RULE_END = "<!-- agent-desktop-router:end -->"
CLAUDE_MATCHER = "Bash|mcp__agent-desktop__.*|mcp__agent_desktop__.*|mcp__lcu__.*"
AGY_MATCHER = "run_command|mcp.*|.*lcu.*|.*agent[-_]desktop.*"


def is_our_wrapper(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size > 8192:
            return False
        head = path.read_bytes()[:512].decode("utf-8", "ignore")
        return WRAPPER_MARKER in head
    except OSError:
        return False


def find_real_agent_desktop(home: Path, env: dict[str, str]) -> Path | None:
    explicit = env.get("ADR_REAL_AGENT_DESKTOP")
    if explicit and Path(explicit).is_file():
        return Path(explicit).resolve()

    candidates: list[Path] = []
    for entry in (env.get("PATH") or "").split(os.pathsep):
        if entry:
            candidates.append(Path(entry) / "agent-desktop")
    candidates.extend([
        home / ".npm-global" / "lib" / "node_modules" / "agent-desktop" / "bin" / "agent-desktop",
        home / ".npm-global" / "bin" / "agent-desktop",
        Path("/opt/homebrew/bin/agent-desktop"),
        Path("/usr/local/bin/agent-desktop"),
        home / ".cargo" / "bin" / "agent-desktop",
    ])
    seen: set[Path] = set()
    for c in candidates:
        try:
            if not c.exists() or is_our_wrapper(c):
                continue
            resolved = c.resolve()
            if resolved in seen or not resolved.is_file() or is_our_wrapper(resolved):
                continue
            seen.add(resolved)
            return resolved
        except OSError:
            continue
    return None


def find_lcu(home: Path, env: dict[str, str]) -> dict[str, Path] | None:
    explicit = env.get("ADR_LCU_BIN")
    prefixes = []
    if explicit:
        p = Path(explicit)
        if p.is_file():
            prefixes.append(p.parent.parent)
    prefixes.extend([
        home / ".local" / "share" / "lcu" / "current",
        Path("/opt/lcu/current"),
        home / "lcu-test" / "install" / "current",
    ])
    which_lcu = shutil.which("lcu", path=env.get("PATH"))
    if which_lcu:
        prefixes.append(Path(which_lcu).resolve().parent.parent)

    for pref in prefixes:
        lcu_bin = pref / "bin" / "lcu"
        if not lcu_bin.is_file():
            continue
        node_candidates = [
            Path("/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node"),
            pref / "agent-tools" / "node" / "bin" / "node",
        ]
        node_bin = next((n for n in node_candidates if n.exists()), None)
        agy_adapter = pref / "adapters" / "antigravity.mjs"
        claude_adapter = pref / "adapters" / "claude.mjs"
        info: dict[str, Path] = {"prefix": pref, "lcu": lcu_bin}
        if node_bin:
            info["node"] = node_bin
        if agy_adapter.is_file():
            info["antigravity_adapter"] = agy_adapter
        if claude_adapter.is_file():
            info["claude_adapter"] = claude_adapter
        return info
    return None


def detect_agents(home: Path) -> list[str]:
    found = []
    if (home / ".claude").is_dir() or Path("/Applications/Claude.app").exists():
        found.append("claude")
    if (home / ".codex").is_dir() or Path("/Applications/ChatGPT.app").exists():
        found.append("codex")
    if (home / ".gemini" / "config").is_dir() or Path("/Applications/Antigravity.app").exists():
        found.append("antigravity")
    return found or ["claude", "codex", "antigravity"]


def upsert_rule_block(target: Path, snippet: str, dry_run: bool) -> str:
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    pattern = re.compile(re.escape(RULE_START) + r".*?" + re.escape(RULE_END), re.S)
    if pattern.search(existing):
        updated = pattern.sub(snippet.strip(), existing)
        action = "updated" if updated != existing else "unchanged"
    else:
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ("\n" if existing and not existing.endswith("\n") else "")
        updated = existing + sep + snippet.strip() + "\n"
        action = "appended"
    if not dry_run and action != "unchanged":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding="utf-8")
    return action


def install_claude(home: Path, guard_py: Path, skill_src: Path, snippet: str, install_rules: bool,
                   codex_rules_installed: bool, dry_run: bool) -> dict[str, Any]:
    claude_dir = home / ".claude"
    settings_path = claude_dir / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))

    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    guard_cmd_entry = {
        "matcher": CLAUDE_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": "/usr/bin/python3",
                "args": ["-I", str(guard_py), "--protocol", "claude"],
                "timeout": 10,
            }
        ],
    }
    filtered = []
    for item in pre:
        item_str = json.dumps(item)
        if "agent-desktop-router" in item_str and "guard.py" in item_str:
            continue
        filtered.append(item)
    filtered.insert(0, guard_cmd_entry)
    hooks["PreToolUse"] = filtered

    if not dry_run:
        claude_dir.mkdir(parents=True, exist_ok=True)
        if settings_path.is_file():
            bak_dir = claude_dir / "backups"
            bak_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(settings_path, bak_dir / f"settings.json.bak-adr-{int(time.time())}")
        tmp = settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(settings_path)

        dst_skill = claude_dir / "skills" / "desktop-browser-routing"
        dst_skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_src, dst_skill, dirs_exist_ok=True)

    rule_status = "skipped"
    if install_rules:
        claude_md = claude_dir / "CLAUDE.md"
        existing = claude_md.read_text(encoding="utf-8") if claude_md.is_file() else ""
        if codex_rules_installed and ".codex/AGENTS.md" in existing:
            rule_status = "inherited_from_codex_agents_md"
        else:
            rule_status = upsert_rule_block(claude_md, snippet, dry_run)

    return {"settings": str(settings_path), "rule": rule_status}


def install_codex(home: Path, skill_src: Path, snippet: str, install_rules: bool, dry_run: bool) -> dict[str, Any]:
    codex_dir = home / ".codex"
    if not dry_run:
        dst_skill = codex_dir / "skills" / "desktop-browser-routing"
        dst_skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_src, dst_skill, dirs_exist_ok=True)

    rule_status = "skipped"
    if install_rules:
        rule_status = upsert_rule_block(codex_dir / "AGENTS.md", snippet, dry_run)
    return {"agents_md": str(codex_dir / "AGENTS.md"), "rule": rule_status}


def install_antigravity(home: Path, guard_py: Path, skill_src: Path, snippet: str,
                        lcu_info: dict[str, Path] | None, codex_rules_installed: bool,
                        dry_run: bool) -> dict[str, Any]:
    plugin_dir = home / ".gemini" / "config" / "plugins" / "agent-desktop-router"
    has_lcu_agy = bool(lcu_info and "node" in lcu_info and "antigravity_adapter" in lcu_info)

    hooks_obj: dict[str, Any] = {
        "desktop-ax-guard": {
            "PreToolUse": [
                {
                    "matcher": AGY_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'/usr/bin/python3 -I "{guard_py}" --protocol antigravity',
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    }
    if has_lcu_agy and lcu_info is not None:
        hook_cmd = f'"{lcu_info["node"]}" "{lcu_info["antigravity_adapter"]}" --hook'
        hooks_obj["lcu"] = {
            "SessionStart": [{"type": "command", "command": hook_cmd, "timeout": 10}],
            "PreInvocation": [{"type": "command", "command": hook_cmd, "timeout": 30}],
            "Stop": [{"type": "command", "command": hook_cmd, "timeout": 60}],
        }

    plugin_manifest = {
        "name": "agent-desktop-router",
        "displayName": "Agent Desktop & Browser Router",
        "version": "0.1.0",
        "description": "Three-tier desktop & browser routing with PreToolUse safety guard and optional LCU Computer Use integration.",
        "logo": "assets/logo.svg",
        "suggestedPrompts": [
            "Check agent-desktop and LCU status and macOS permissions",
            "Read Obsidian window structure in the background using agent-desktop",
            "Operate desktop app following the three-tier routing workflow",
        ],
    }

    codex_agents_md = home / ".codex" / "AGENTS.md"
    if codex_rules_installed and codex_agents_md.is_file():
        rules_text = f"@[Global Shared Rules]({codex_agents_md})\n"
    else:
        rules_text = snippet.strip() + "\n"

    if not dry_run:
        (plugin_dir / "assets").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "rules").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "skills").mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "templates" / "antigravity-logo.svg", plugin_dir / "assets" / "logo.svg")
        (plugin_dir / "plugin.json").write_text(json.dumps(plugin_manifest, indent=2) + "\n", encoding="utf-8")
        (plugin_dir / "hooks.json").write_text(json.dumps(hooks_obj, indent=2) + "\n", encoding="utf-8")
        (plugin_dir / "rules" / "AGENTS.md").write_text(rules_text, encoding="utf-8")

        dst_skill = plugin_dir / "skills" / "desktop-browser-routing"
        shutil.copytree(skill_src, dst_skill, dirs_exist_ok=True)

        if has_lcu_agy and lcu_info is not None:
            mcp_cfg = {
                "mcpServers": {
                    "lcu": {
                        "command": str(lcu_info["node"]),
                        "args": [str(lcu_info["antigravity_adapter"]), str(lcu_info["lcu"])],
                        "timeoutSeconds": 3600,
                        "tools": {"js": {"eager": True}, "js_reset": {"eager": True}},
                    }
                }
            }
            (plugin_dir / "mcp_config.json").write_text(json.dumps(mcp_cfg, indent=2) + "\n", encoding="utf-8")

    return {"plugin_dir": str(plugin_dir), "lcu_wired": has_lcu_agy}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install agent-desktop-router for macOS AI coding agents.")
    p.add_argument("--agents", default="auto", help="Comma-separated list: auto, all, or claude,codex,antigravity")
    p.add_argument("--lang", choices=("en", "zh"), default="en", help="Language for injected rule snippets")
    p.add_argument("--home", default=None, help="Override HOME directory (for isolated testing)")
    p.add_argument("--prefix", default=None, help="Install prefix (default: ~/.local/share/agent-desktop-router)")
    p.add_argument("--config-dir", default=None, help="Config directory (default: ~/.config/agent-desktop-router)")
    p.add_argument("--bin-dir", default=None, help="CLI wrapper directory (default: ~/.local/bin)")
    p.add_argument("--no-rules", action="store_true", help="Do not inject routing rules into AGENTS.md / CLAUDE.md")
    p.add_argument("--skip-compile-probe", action="store_true", help="Skip compiling probe.swift (testing only)")
    p.add_argument("--dry-run", action="store_true", help="Preview planned actions without writing files")
    p.add_argument("--json", action="store_true", help="Print summary as JSON")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else home / ".local" / "share" / "agent-desktop-router"
    config_dir = Path(args.config_dir).expanduser().resolve() if args.config_dir else home / ".config" / "agent-desktop-router"
    bin_dir = Path(args.bin_dir).expanduser().resolve() if args.bin_dir else home / ".local" / "bin"

    if args.agents == "auto":
        agents = detect_agents(home)
    elif args.agents == "all":
        agents = ["claude", "codex", "antigravity"]
    else:
        agents = [a.strip().lower() for a in args.agents.split(",") if a.strip()]
    for a in agents:
        if a not in ("claude", "codex", "antigravity"):
            raise SystemExit(f"Unsupported agent: {a!r} (choose from claude, codex, antigravity)")

    snippet_file = "rules-snippet.zh-CN.md" if args.lang == "zh" else "rules-snippet.md"
    snippet = (REPO_ROOT / "templates" / snippet_file).read_text(encoding="utf-8")
    skill_src = REPO_ROOT / "skills" / "desktop-browser-routing"
    guard_dst = prefix / "src" / "guard.py"
    probe_dst = prefix / "src" / "probe"
    cfg_dst = config_dir / "blocked-apps.json"

    if not args.dry_run:
        (prefix / "src").mkdir(parents=True, exist_ok=True)
        (prefix / "config").mkdir(parents=True, exist_ok=True)
        (prefix / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "src" / "guard.py", guard_dst)
        shutil.copy2(REPO_ROOT / "src" / "probe.swift", prefix / "src" / "probe.swift")
        shutil.copy2(REPO_ROOT / "config" / "blocked-apps.default.json", prefix / "config" / "blocked-apps.default.json")
        for sc in ("doctor.py", "uninstall.py"):
            src_sc = REPO_ROOT / "scripts" / sc
            if src_sc.is_file():
                shutil.copy2(src_sc, prefix / "scripts" / sc)

        if not args.skip_compile_probe:
            if sys.platform != "darwin":
                raise SystemExit("agent-desktop-router v0.1 currently requires macOS to compile probe.swift")
            subprocess.run(["swiftc", "-O", "-o", str(probe_dst), str(prefix / "src" / "probe.swift")], check=True)
            os.chmod(probe_dst, 0o755)

        config_dir.mkdir(parents=True, exist_ok=True)
        if not cfg_dst.is_file():
            shutil.copy2(REPO_ROOT / "config" / "blocked-apps.default.json", cfg_dst)

    real_ad = find_real_agent_desktop(home, os.environ)
    wrapper_installed = None
    if real_ad is not None:
        wrapper_path = bin_dir / "agent-desktop"
        wrapper_installed = str(wrapper_path)
        if not args.dry_run:
            bin_dir.mkdir(parents=True, exist_ok=True)
            wrapper_body = (
                f"#!/bin/sh\n{WRAPPER_MARKER}\n"
                f'ADR_CONFIG="{cfg_dst}" /usr/bin/python3 -I "{guard_dst}" --cli-check "$@" || exit $?\n'
                f'exec "{real_ad}" "$@"\n'
            )
            tmp_w = wrapper_path.with_suffix(".tmp")
            tmp_w.write_text(wrapper_body, encoding="utf-8")
            os.chmod(tmp_w, 0o755)
            tmp_w.replace(wrapper_path)

    lcu_info = find_lcu(home, os.environ)
    install_rules = not args.no_rules
    results: dict[str, Any] = {}
    codex_rules_done = False

    if "codex" in agents:
        results["codex"] = install_codex(home, skill_src, snippet, install_rules, args.dry_run)
        codex_rules_done = install_rules

    if "claude" in agents:
        results["claude"] = install_claude(home, guard_dst, skill_src, snippet, install_rules, codex_rules_done, args.dry_run)

    if "antigravity" in agents:
        results["antigravity"] = install_antigravity(home, guard_dst, skill_src, snippet, lcu_info, codex_rules_done, args.dry_run)

    summary = {
        "ok": True,
        "dry_run": args.dry_run,
        "prefix": str(prefix),
        "config": str(cfg_dst),
        "cli_wrapper": wrapper_installed,
        "real_agent_desktop": str(real_ad) if real_ad else None,
        "lcu_detected": str(lcu_info["lcu"]) if lcu_info else None,
        "agents": results,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        mode = "[DRY RUN] " if args.dry_run else ""
        print(f"{mode}agent-desktop-router configured for: {', '.join(agents)}")
        print(f"  Runtime prefix : {prefix}")
        print(f"  Blocked config : {cfg_dst}")
        print(f"  CLI wrapper    : {wrapper_installed or 'skipped (agent-desktop not installed yet)'}")
        print(f"  LCU detected   : {summary['lcu_detected'] or 'not found (optional Tier 3)'}")
        print("Next: run `python3 scripts/doctor.py` to check macOS Accessibility permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
