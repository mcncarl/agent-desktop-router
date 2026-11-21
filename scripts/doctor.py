#!/usr/bin/env python3
"""Health and permission diagnostics for agent-desktop-router."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

WRAPPER_MARKER = "# ADR_MANAGED_WRAPPER_V1"


def check_chatgpt_bundle() -> dict[str, Any]:
    app = Path("/Applications/ChatGPT.app")
    if not app.exists():
        return {"installed": False, "signature_ok": None, "unreadable_files": []}
    unreadable: list[str] = []
    for root, _, files in os.walk(app):
        for fn in files:
            fp = os.path.join(root, fn)
            if not os.access(fp, os.R_OK):
                unreadable.append(fp)
                if len(unreadable) >= 5:
                    break
        if len(unreadable) >= 5:
            break
    sig = subprocess.run(["codesign", "-vv", str(app)], capture_output=True, text=True, timeout=10)
    return {
        "installed": True,
        "signature_ok": sig.returncode == 0,
        "unreadable_files": unreadable,
        "fix_hint": f"sudo chmod a+r {' '.join(unreadable)}" if unreadable else None,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diagnose agent-desktop-router setup and permissions.")
    p.add_argument("--home", default=None, help="Override HOME directory")
    p.add_argument("--prefix", default=None, help="Install prefix")
    p.add_argument("--config-dir", default=None, help="Config directory")
    p.add_argument("--bin-dir", default=None, help="CLI wrapper directory")
    p.add_argument("--json", action="store_true", help="Output JSON report")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else home / ".local" / "share" / "agent-desktop-router"
    config_dir = Path(args.config_dir).expanduser().resolve() if args.config_dir else home / ".config" / "agent-desktop-router"
    bin_dir = Path(args.bin_dir).expanduser().resolve() if args.bin_dir else home / ".local" / "bin"

    checks: dict[str, Any] = {
        "platform": {"system": platform.system(), "machine": platform.machine()},
    }
    issues: list[str] = []

    cfg_path = config_dir / "blocked-apps.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            checks["config"] = {
                "ok": True,
                "path": str(cfg_path),
                "blocked_bundles": len(cfg.get("blocked_bundles", [])),
                "allowed_bundles": len(cfg.get("allowed_bundles", [])),
            }
        except Exception as exc:
            checks["config"] = {"ok": False, "path": str(cfg_path), "error": str(exc)}
            issues.append(f"Invalid JSON in {cfg_path}: {exc}")
    else:
        checks["config"] = {"ok": False, "path": str(cfg_path), "error": "missing"}
        issues.append(f"Config missing at {cfg_path}; run scripts/install.py")

    probe_bin = prefix / "src" / "probe"
    if probe_bin.is_file() and os.access(probe_bin, os.X_OK):
        res = subprocess.run([str(probe_bin), "pid", str(os.getpid())], capture_output=True, text=True, timeout=5)
        checks["probe"] = {"ok": res.returncode == 0, "path": str(probe_bin)}
        if res.returncode != 0:
            issues.append(f"Native probe failed with exit code {res.returncode}")
    else:
        checks["probe"] = {"ok": False, "path": str(probe_bin), "error": "missing_or_not_executable"}
        issues.append(f"Compiled probe missing at {probe_bin}")

    wrapper = bin_dir / "agent-desktop"
    wrapper_ok = wrapper.is_file() and WRAPPER_MARKER in wrapper.read_text(encoding="utf-8", errors="ignore")
    checks["cli_wrapper"] = {"ok": wrapper_ok, "path": str(wrapper)}

    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        st = subprocess.run([str(wrapper), "status"], capture_output=True, text=True, timeout=8)
        try:
            st_json = json.loads(st.stdout)
            ax = st_json.get("data", {}).get("permissions", {}).get("accessibility", {}).get("state")
            checks["agent_desktop"] = {"ok": st_json.get("ok", False), "accessibility": ax}
        except Exception:
            checks["agent_desktop"] = {"ok": False, "raw": st.stdout[:200]}
    else:
        checks["agent_desktop"] = {"ok": False, "note": "agent-desktop wrapper not installed (install vercel-labs/agent-desktop first)"}

    checks["chatgpt_bundle"] = check_chatgpt_bundle()
    if checks["chatgpt_bundle"].get("unreadable_files"):
        issues.append(f"ChatGPT.app has unreadable files breaking LCU codesign check; run: {checks['chatgpt_bundle']['fix_hint']}")

    claude_settings = home / ".claude" / "settings.json"
    claude_hooked = claude_settings.is_file() and "guard.py" in claude_settings.read_text(encoding="utf-8", errors="ignore")
    agy_plugin = (home / ".gemini" / "config" / "plugins" / "agent-desktop-router" / "plugin.json").is_file()
    codex_rules = (home / ".codex" / "AGENTS.md").is_file() and "agent-desktop-router:start" in (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    checks["hosts"] = {
        "claude_pretool_hook": claude_hooked,
        "antigravity_plugin": agy_plugin,
        "codex_rules": codex_rules,
    }

    report = {"ok": len(issues) == 0, "issues": issues, "checks": checks}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"agent-desktop-router doctor: {'OK' if report['ok'] else 'ISSUES FOUND'}")
        print(f"  Config        : {'ok' if checks['config']['ok'] else 'MISSING/INVALID'} ({cfg_path})")
        print(f"  Native probe  : {'ok' if checks['probe']['ok'] else 'MISSING'} ({probe_bin})")
        print(f"  CLI wrapper   : {'ok' if wrapper_ok else 'not installed'} ({wrapper})")
        print(f"  Accessibility : {checks['agent_desktop'].get('accessibility', 'n/a')}")
        print(f"  Hosts wired   : claude={claude_hooked}, codex={codex_rules}, antigravity={agy_plugin}")
        for item in issues:
            print(f"  [!] {item}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
