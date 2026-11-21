#!/usr/bin/env python3
"""PreToolUse & CLI safety guard for desktop and Computer Use automation.

Supports three invocation protocols from one zero-dependency script:
  1. Claude Code PreToolUse (`--protocol claude`, default stdin JSON)
  2. Google Antigravity PreToolUse (`--protocol antigravity`)
  3. Direct CLI wrapper check (`--cli-check <subcommand> [args...]`) for Codex / shell

Safety guarantees against accidental UI automation mistakes:
  - Blocks all apps, bundle IDs, and web surfaces configured in `blocked-apps.json`
    (while honoring explicit `allowed_bundles` and `allowed_terms` exemptions such as WeCom).
  - Blocks Notification Center when `block_notification_center` is enabled (prevents reading
    or clicking private message banners).
  - Requires an explicit `--app`, `--window-id`, or `@snapshot:eN` reference on scoped
    `agent-desktop` commands so actions never fall back blindly to the frontmost app.
  - Verifies `@snapshot:eN` element PIDs, `w-<num>` window owners, and `x,y` screen coordinates
    against the native macOS `probe` inspector before allowing execution.
  - Rejects uninspectable shell wrappers (`batch`, `xargs`, `$VAR` targets, inline scripts).
  - Fails closed if `blocked-apps.json` cannot be parsed when a desktop/LCU tool is called.

Environment overrides (primarily for testing):
  ADR_CONFIG  Path to blocked-apps.json
  ADR_PROBE   Path to compiled `probe` binary
  ADR_STATE   Path to `~/.agent-desktop` snapshot directory
  ADR_LOG     Path to audit log file
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_USER_CONFIG = os.path.expanduser("~/.config/agent-desktop-router/blocked-apps.json")
BUNDLED_CONFIG = os.path.join(REPO_ROOT, "config", "blocked-apps.default.json")

PROBE = os.environ.get("ADR_PROBE") or os.path.join(HERE, "probe")
STATE = os.environ.get("ADR_STATE") or os.path.expanduser("~/.agent-desktop")
LOG = os.environ.get("ADR_LOG") or os.path.expanduser("~/.local/state/agent-desktop-router/guard.log")

NOTIF_RE = re.compile(r"通知中心|notification[\s_-]*center|notificationcenterui", re.I)
AD_TEXT = re.compile(r"agent[-_]desktop", re.I)
AD_BIN = re.compile(r"^agent[-_]desktop([-_][\w.]+)?$", re.I)
REF_TOKEN = re.compile(r"@([A-Za-z0-9]+):(e\d+)")
WINDOW_ID = re.compile(r"^w-(\d+)$")

INFO = {
    "status", "permissions", "version", "skills", "list-apps", "list-windows",
    "list-displays", "help", "session", "trace", "cursor-overlay",
    "clipboard-get", "clipboard-set", "clipboard-clear",
}
NEVER = {"batch", "mcp", "key-down", "key-up", "mouse-down", "mouse-up"}
REF_CMDS = {
    "click", "double-click", "triple-click", "right-click", "type", "set-value",
    "clear", "focus", "select", "toggle", "check", "uncheck", "expand", "collapse",
    "scroll", "scroll-to", "get", "is", "hover", "drag",
}
SCOPED = {
    "snapshot", "find", "screenshot", "list-surfaces", "focus-window", "resize-window",
    "move-window", "minimize", "maximize", "restore", "press", "wait",
}
APP_POSITIONAL = {"launch", "close-app"}
COORD_CMDS = {"mouse-click", "mouse-move", "mouse-wheel"}
NOTIF_CMDS = {
    "list-notifications", "dismiss-notification", "dismiss-all-notifications",
    "notification-action",
}
OTHER_OK = {"open-system-surface"}
KNOWN = INFO | NEVER | REF_CMDS | SCOPED | APP_POSITIONAL | COORD_CMDS | NOTIF_CMDS | OTHER_OK
SYSTEM_SURFACES = {"dock", "menu-bar-extras"}

VALUE_OPTS = {
    "--app", "--window-id", "--root", "--surface", "--xy", "--from", "--to",
    "--from-xy", "--to-xy", "--x", "--y", "--session", "--agent-id", "--trace",
    "--screenshot", "--wait-for", "--wait-for-gone", "--wait-timeout",
    "--timeout-ms", "--timeout", "--title", "--text", "--limit", "--expected-app",
    "--expected-title", "--max-depth", "--button", "--count", "--modifiers",
    "--interval-ms", "--arg", "--env", "--cwd", "--element", "--window", "--event",
    "--predicate", "--direction", "--amount", "--width", "--height",
}
FREE_TEXT_OPTS = {
    "--trace", "--screenshot", "--text", "--title", "--expected-title",
    "--wait-for", "--wait-for-gone",
}

SHELLS = {"bash", "sh", "zsh", "dash", "fish"}
INTERPRETERS = SHELLS | {
    "python", "python3", "node", "osascript", "ruby", "perl", "swift",
    "deno", "bun", "tsx", "ts-node", "php", "lua",
}
READONLY = {
    "cat", "grep", "egrep", "fgrep", "rg", "head", "tail", "less", "more", "ls",
    "which", "whereis", "type", "file", "stat", "wc", "diff", "jq", "readlink",
    "realpath", "du", "trash", "echo", "printf", "git", "test", "[", "mdfind",
    "sort", "uniq", "cut", "awk", "sed", "tee", "open", "memoryctl",
}
NPM_SAFE = {
    "install", "i", "uninstall", "un", "remove", "rm", "ls", "list", "view",
    "info", "update", "outdated", "root", "prefix", "config",
}
WRAPPERS = {"env", "command", "exec", "nohup", "time", "sudo", "caffeinate", "nice", "builtin"}
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "}", "done", "fi", "esac", "in"}


class Deny(Exception):
    """Raised when a command or tool invocation violates desktop safety policy."""


class PolicyRuleSet:
    def __init__(self, data: dict[str, Any]):
        self.block_notif = bool(data.get("block_notification_center", True))
        self.blocked_bundles = {str(b).strip().lower() for b in data.get("blocked_bundles", []) if str(b).strip()}
        self.allowed_bundles = {str(b).strip().lower() for b in data.get("allowed_bundles", []) if str(b).strip()}

        patterns = [str(p) for p in data.get("blocked_patterns", []) if str(p).strip()]
        for b in self.blocked_bundles:
            patterns.append(re.escape(b))
        self.blocked_re = re.compile("|".join(patterns), re.I) if patterns else None

        # Sort allowed_terms longest-first so compound terms (e.g. "企业微信") strip cleanly before shorter tokens.
        terms = sorted(
            {str(t) for t in data.get("allowed_terms", []) if str(t).strip()},
            key=len,
            reverse=True,
        )
        self.allowed_re = re.compile("|".join(re.escape(t) for t in terms), re.I) if terms else None

    def strip_allowed(self, text: str) -> str:
        if not text or not self.allowed_re:
            return text or ""
        return self.allowed_re.sub(" ", text)

    def is_blocked_text(self, text: str) -> bool:
        cleaned = self.strip_allowed(text)
        if self.block_notif and NOTIF_RE.search(cleaned):
            return True
        if self.blocked_re and self.blocked_re.search(cleaned):
            return True
        return False

    def is_blocked_probe(self, out: str) -> bool:
        for line in (out or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                bundle = parts[2].strip().lower()
                if bundle and bundle in self.allowed_bundles:
                    continue
                if bundle and any(bundle == b or bundle.startswith(b + ".") for b in self.blocked_bundles):
                    return True
            if self.is_blocked_text(line):
                return True
        return False


_POLICY_CACHE: PolicyRuleSet | None = None


def load_policy() -> PolicyRuleSet:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    cfg_path = os.environ.get("ADR_CONFIG")
    if not cfg_path:
        cfg_path = DEFAULT_USER_CONFIG if os.path.isfile(DEFAULT_USER_CONFIG) else BUNDLED_CONFIG
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("top-level JSON must be an object")
    except Exception as exc:
        raise Deny(f"Failed to load blocked-apps config ({cfg_path}): {exc}") from exc
    _POLICY_CACHE = PolicyRuleSet(data)
    return _POLICY_CACHE


def log(decision: str, detail: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), mode=0o700, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{decision}\t{detail[:300]}\n")
    except OSError:
        pass


def run_probe(*args: Any) -> tuple[int, str]:
    try:
        out = subprocess.run([PROBE, *map(str, args)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Deny(f"Window probe failed ({exc}); refusing unverified UI action") from exc
    return out.returncode, out.stdout


# ---------- agent-desktop invocation checks ----------

def check_ref(snap: str, elem: str, policy: PolicyRuleSet) -> None:
    candidates = [os.path.join(STATE, "snapshots", snap, "refmap.json")]
    if os.path.isdir(STATE):
        for root, _, files in os.walk(STATE):
            if os.path.basename(root) == snap and "refmap.json" in files:
                candidates.append(os.path.join(root, "refmap.json"))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            entry = (json.load(f).get("inner") or {}).get("@" + elem)
        if entry is None:
            continue
        pid = entry.get("pid")
        if pid is not None:
            code, out = run_probe("pid", pid)
            if code == 0:
                parts = out.strip().split("\t")
                bundle = parts[2].strip().lower() if len(parts) >= 3 else ""
                if bundle and bundle in policy.allowed_bundles:
                    return
                if policy.is_blocked_probe(out):
                    raise Deny(f"Ref @{snap}:{elem} belongs to a blocked application process")
        fields = " ".join(str(entry.get(k, "")) for k in ("name", "description", "source_app", "role"))
        if policy.is_blocked_text(fields):
            raise Deny(f"Ref @{snap}:{elem} targets a blocked app or Notification Center element")
        return
    raise Deny(f"Snapshot record for @{snap}:{elem} not found; take a fresh snapshot first")


def check_window(value: str, policy: PolicyRuleSet) -> None:
    m = WINDOW_ID.match(value)
    if not m:
        raise Deny(f"--window-id must be a literal w-<number>, got {value!r}")
    code, out = run_probe("window", m.group(1))
    if code != 0:
        raise Deny(f"Window {value} does not exist; cannot verify owner")
    if policy.is_blocked_probe(out):
        raise Deny(f"Window {value} belongs to a blocked application or Notification Center")


def check_point(xy: str, policy: PolicyRuleSet) -> None:
    try:
        x, y = (float(v) for v in xy.split(","))
    except ValueError as exc:
        raise Deny(f"Coordinates must be literal x,y numbers, got {xy!r}") from exc
    _, out = run_probe("at", x, y)
    if policy.is_blocked_probe(out):
        raise Deny(f"Screen point {xy} overlaps a blocked application window")


def parse_invocation(args: list[str]) -> tuple[dict[str, list[str]], list[str], set[str]]:
    opts: dict[str, list[str]] = {}
    positionals: list[str] = []
    flags: set[str] = set()
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            positionals.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if eq:
                opts.setdefault(name, []).append(val)
            elif name in VALUE_OPTS and i + 1 < len(args):
                opts.setdefault(name, []).append(args[i + 1])
                i += 1
            else:
                flags.add(name)
        elif a.startswith("-") and len(a) > 1 and not REF_TOKEN.search(a):
            flags.add(a)
        else:
            positionals.append(a)
        i += 1
    return opts, positionals, flags


def check_invocation(args: list[str]) -> None:
    if not args or args[0] in ("-h", "--help", "-V", "--version", "help"):
        return
    policy = load_policy()
    joined = " ".join(args)
    if policy.is_blocked_text(joined):
        raise Deny("agent-desktop arguments reference a blocked application or Notification Center")
    opts, positionals, flags = parse_invocation(args)
    sub = positionals[0] if positionals else None
    if sub is None:
        return
    if sub not in KNOWN:
        raise Deny(f"Unknown agent-desktop subcommand {sub!r}")
    if "--help" in flags or "-h" in flags:
        return
    if sub in NEVER:
        raise Deny(f"Subcommand {sub!r} bypasses per-action verification and is disabled")

    free_values = {v for k in FREE_TEXT_OPTS for v in opts.get(k, [])}
    free_positionals = set(positionals[2:]) if sub in ("type", "set-value", "clipboard-set") else set()
    if sub == "screenshot":
        free_positionals |= set(positionals[1:])
    for tok in args:
        if ("$" in tok or "`" in tok) and tok not in free_values and tok not in free_positionals:
            raise Deny(f"Target argument contains shell variable/substitution ({tok}); pass a literal value")

    refs = []
    for tok in args:
        for snap, elem in REF_TOKEN.findall(tok):
            check_ref(snap, elem, policy)
            refs.append((snap, elem))
    for value in opts.get("--window-id", []):
        check_window(value, policy)
    for key in ("--xy", "--from-xy", "--to-xy"):
        for value in opts.get(key, []):
            check_point(value, policy)
    if "--x" in opts or "--y" in opts:
        check_point(f"{(opts.get('--x') or [''])[0]},{(opts.get('--y') or [''])[0]}", policy)
    surfaces = opts.get("--surface", [])
    if policy.block_notif and any(s == "notification-center" for s in surfaces):
        raise Deny("Notification Center surface is blocked by policy")

    has_app = bool(opts.get("--app"))
    has_window = bool(opts.get("--window-id"))
    has_coords = any(k in opts for k in ("--xy", "--from-xy", "--to-xy", "--x", "--y"))

    if sub in INFO or sub in OTHER_OK:
        return
    if sub in NOTIF_CMDS:
        if not (opts.get("--app") or opts.get("--expected-app")):
            raise Deny(f"{sub} requires --app or --expected-app naming a non-blocked app")
        return
    if sub in APP_POSITIONAL:
        if len(positionals) < 2:
            raise Deny(f"{sub} requires an explicit application name")
        return
    if sub in COORD_CMDS:
        if not has_coords:
            raise Deny(f"{sub} requires literal screen coordinates")
        return
    if sub in REF_CMDS:
        if not (refs or has_coords):
            raise Deny(f"{sub} requires a literal @snapshot:eN ref or coordinates")
        return
    if sub == "focus-window":
        if not (has_app or has_window):
            raise Deny("focus-window requires --app or --window-id (cannot match by title alone)")
        return
    if sub == "wait":
        needs_target = any(k in opts for k in ("--text", "--element", "--predicate")) or "--menu" in flags
        if needs_target and not (has_app or has_window or refs):
            raise Deny("wait inspecting UI content requires --app or --window-id")
        return
    if sub in SCOPED:
        if has_app or has_window or refs:
            return
        if sub in ("snapshot", "find", "list-surfaces") and surfaces and all(s in SYSTEM_SURFACES for s in surfaces):
            return
        raise Deny(f"{sub} requires an explicit --app or --window-id (cannot target frontmost app blindly)")


# ---------- Shell command analysis ----------

def split_unquoted_newlines(cmd: str) -> str:
    out, quote, escape = [], None, False
    for ch in cmd:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and quote != "'":
            out.append(ch)
            escape = True
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "\n":
            out.append(" ; ")
            continue
        out.append(ch)
    return "".join(out)


def segments(cmd: str) -> list[list[str]]:
    lex = shlex.shlex(split_unquoted_newlines(cmd), posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    segs: list[list[str]] = []
    cur: list[str] = []
    skip_next = False
    for tok in lex:
        if skip_next:
            skip_next = False
            if not (tok and set(tok) <= set(";&|()<>")):
                continue
        is_punct = bool(tok) and set(tok) <= set(";&|()<>")
        if is_punct and ("(" in tok or ")" in tok or set(tok) <= set(";&|")):
            if cur:
                segs.append(cur)
            cur = []
            continue
        if is_punct:
            if cur and cur[-1].isdigit():
                cur.pop()
            if tok not in ("<<", "<<<"):
                skip_next = True
            continue
        cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def file_mentions_ad(path: str, cwd: str | None) -> bool:
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    real = os.path.realpath(p)
    if real.startswith(os.path.realpath(REPO_ROOT) + os.sep) or real.startswith(os.path.realpath(HERE) + os.sep):
        return False
    try:
        if not os.path.isfile(p) or os.path.getsize(p) > 4_000_000:
            return False
        with open(p, "rb") as f:
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return False
    return bool(AD_TEXT.search(data))


def check_segment(tokens: list[str], cwd: str | None, depth: int) -> None:
    while tokens and (tokens[0] in KEYWORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])):
        tokens = tokens[1:]
    while tokens and os.path.basename(tokens[0]) in WRAPPERS:
        tokens = tokens[1:]
        while tokens and (tokens[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])):
            tokens = tokens[1:]
    if not tokens:
        return
    word = tokens[0]
    base = os.path.basename(word)
    text = " ".join(tokens)

    if AD_BIN.match(base):
        check_invocation(tokens[1:])
        return
    if base in ("npx", "bunx", "pnpx") or (
        base in ("npm", "pnpm", "yarn") and len(tokens) > 1 and tokens[1] in ("exec", "x", "dlx")
    ):
        for i, t in enumerate(tokens[1:], start=1):
            if AD_BIN.match(os.path.basename(t)):
                rest = tokens[i + 1:]
                check_invocation(rest[1:] if rest[:1] == ["--"] else rest)
                return
        if AD_TEXT.search(text):
            raise Deny("Invoke agent-desktop directly rather than through npx/package wrappers")
        return
    if base in ("npm", "pnpm", "yarn"):
        if len(tokens) > 1 and tokens[1] not in NPM_SAFE and AD_TEXT.search(text):
            raise Deny("Package manager subcommand may invoke agent-desktop indirectly")
        return
    if base == "xargs":
        if AD_TEXT.search(text):
            raise Deny("Do not invoke agent-desktop via xargs")
        return
    if base in INTERPRETERS:
        for flag in ("-c", "-e"):
            if flag in tokens:
                idx = tokens.index(flag)
                code = tokens[idx + 1] if idx + 1 < len(tokens) else ""
                if base in SHELLS and depth < 3:
                    check_command(code, cwd, depth + 1)
                elif AD_TEXT.search(code):
                    raise Deny("Inline interpreter script references agent-desktop; invoke CLI directly")
        for t in tokens[1:]:
            if not t.startswith("-") and file_mentions_ad(t, cwd):
                raise Deny(f"Script {t} references agent-desktop; invoke CLI directly")
        return
    if ("/" in word or os.path.exists(os.path.join(cwd or "", word))) and file_mentions_ad(word, cwd):
        raise Deny(f"Executable {word} references agent-desktop; invoke CLI directly")
    if base in READONLY or base == "cd":
        return
    if AD_TEXT.search(text):
        raise Deny(f"agent-desktop appears in unverified command ({base}); invoke CLI directly")


def substitutions(cmd: str) -> list[str]:
    bodies = re.findall(r"`([^`]*)`", cmd)
    i = 0
    while True:
        start = cmd.find("$(", i)
        if start < 0:
            break
        depth, j = 1, start + 2
        while j < len(cmd) and depth:
            depth += {"(": 1, ")": -1}.get(cmd[j], 0)
            j += 1
        bodies.append(cmd[start + 2:j - 1])
        i = start + 2
    return bodies


def check_command(cmd: str, cwd: str | None, depth: int = 0) -> None:
    if depth < 4:
        for body in substitutions(cmd):
            if AD_TEXT.search(body):
                check_command(body, cwd, depth + 1)
    try:
        segs = segments(cmd)
    except ValueError as exc:
        if AD_TEXT.search(cmd):
            raise Deny("Unparseable shell command referencing agent-desktop") from exc
        return
    for tokens in segs:
        check_segment(tokens, cwd, depth)


def check_mcp_agent_desktop(tool_name: str, tool_input: dict[str, Any]) -> None:
    policy = load_policy()
    raw = json.dumps(tool_input, ensure_ascii=False)
    if policy.is_blocked_text(raw):
        raise Deny("agent-desktop MCP arguments reference a blocked app or Notification Center")
    sub = tool_name.split("__")[-1]
    sub = sub[len("desktop_"):] if sub.startswith("desktop_") else sub
    args = [sub.replace("_", "-")]
    for key, value in (tool_input or {}).items():
        values = value if isinstance(value, list) else [value]
        if key in ("ref", "ref_id", "target"):
            args.extend(str(v) for v in values)
            continue
        for v in values:
            if isinstance(v, bool):
                if v:
                    args.append("--" + key.replace("_", "-"))
            else:
                args.extend(["--" + key.replace("_", "-"), str(v)])
    check_invocation(args)


def check_lcu_mcp(tool_input: dict[str, Any]) -> None:
    policy = load_policy()
    raw = json.dumps(tool_input, ensure_ascii=False)
    if policy.is_blocked_text(raw):
        raise Deny("LCU Computer Use call references a blocked app or Notification Center")


# ---------- Protocol Entrypoints ----------

def run_claude_hook() -> int:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    try:
        if tool == "Bash":
            cmd = tool_input.get("command", "")
            check_command(cmd, payload.get("cwd"))
            if AD_TEXT.search(cmd):
                log("claude-allow", cmd.replace("\n", " "))
        elif re.match(r"^mcp__agent[-_]desktop__", tool):
            check_mcp_agent_desktop(tool, tool_input)
            log("claude-allow", f"{tool} {json.dumps(tool_input, ensure_ascii=False)}")
        elif re.match(r"^mcp__.*lcu__", tool):
            check_lcu_mcp(tool_input)
    except Deny as d:
        reason = f"[Desktop Guard] {d}"
        log("claude-deny", f"{reason} :: {json.dumps(tool_input, ensure_ascii=False)}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, ensure_ascii=False))
    except Exception as exc:
        raw = json.dumps(tool_input, ensure_ascii=False)
        if AD_TEXT.search(raw) or tool.startswith("mcp__agent") or "lcu" in tool.lower():
            reason = f"[Desktop Guard] Internal error ({type(exc).__name__}: {exc}); failing closed"
            log("claude-deny", reason)
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }, ensure_ascii=False))
    return 0


def run_antigravity_hook() -> int:
    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        print(json.dumps({"decision": "allow"}))
        return 0
    try:
        payload = json.loads(raw_stdin)
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return 0

    tool_call = payload.get("toolCall") or {}
    name = tool_call.get("name") or payload.get("tool_name") or ""
    args = tool_call.get("args") or payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    lower_name = name.lower()
    server_name = str(args.get("ServerName") or args.get("serverName") or "").lower()
    tool_args = args.get("Arguments") if isinstance(args.get("Arguments"), dict) else args

    try:
        if name == "run_command" or "CommandLine" in args:
            cmd = args.get("CommandLine") or args.get("command") or ""
            check_command(cmd, cwd)
            if AD_TEXT.search(cmd):
                log("agy-allow", cmd.replace("\n", " "))
        elif re.search(r"agent[-_]desktop", lower_name) or re.search(r"agent[-_]desktop", server_name):
            sub_tool = str(args.get("ToolName") or args.get("toolName") or name)
            check_mcp_agent_desktop(f"mcp__agent-desktop__{sub_tool.split('__')[-1]}", tool_args)
            log("agy-allow", f"{name} {json.dumps(tool_args, ensure_ascii=False)}")
        elif "lcu" in lower_name or "lcu" in server_name:
            check_lcu_mcp(tool_args)
        print(json.dumps({"decision": "allow"}))
    except Deny as d:
        reason = f"[Desktop Guard] {d}"
        log("agy-deny", f"{reason} :: {json.dumps(args, ensure_ascii=False)}")
        print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    except Exception as exc:
        raw = json.dumps(args, ensure_ascii=False)
        if AD_TEXT.search(raw) or "agent" in lower_name or "lcu" in lower_name:
            reason = f"[Desktop Guard] Internal error ({type(exc).__name__}: {exc}); failing closed"
            log("agy-deny", reason)
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
        else:
            print(json.dumps({"decision": "allow"}))
    return 0


def run_cli_check(argv: list[str]) -> int:
    try:
        check_invocation(argv)
        log("cli-allow", " ".join(argv))
        return 0
    except Deny as d:
        reason = f"[Desktop Guard] {d}"
        log("cli-deny", f"{reason} :: {' '.join(argv)}")
        print(json.dumps({
            "version": "2.6",
            "ok": False,
            "error": {"code": "POLICY_DENIED", "message": reason},
        }, ensure_ascii=False))
        return 2
    except Exception as exc:
        reason = f"[Desktop Guard] Internal error ({type(exc).__name__}: {exc}); failing closed"
        log("cli-deny", reason)
        print(json.dumps({
            "version": "2.6",
            "ok": False,
            "error": {"code": "POLICY_DENIED", "message": reason},
        }, ensure_ascii=False))
        return 2


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--cli-check":
        return run_cli_check(sys.argv[2:])
    protocol = "claude"
    if len(sys.argv) >= 3 and sys.argv[1] == "--protocol":
        protocol = sys.argv[2].lower()
    if protocol in ("antigravity", "agy"):
        return run_antigravity_hook()
    return run_claude_hook()


if __name__ == "__main__":
    raise SystemExit(main())
