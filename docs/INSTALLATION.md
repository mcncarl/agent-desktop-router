# Installation & Agent Onboarding Guide (`agent-desktop-router`)

This runbook can be executed directly by a human in a terminal **or** by an AI coding agent (**Claude Code**, **Codex**, or **Google Antigravity**).

> **Agent Execution Contract**: Follow the steps below idempotently. Never modify or delete unrelated user hooks or files. When macOS Accessibility or Touch ID permissions are required, pause and guide the user clearly.

---

## 1. Prerequisites

- **OS**: macOS 13+ with Xcode Command Line Tools (`swiftc --version` and `/usr/bin/python3` available).
- **Tier 2 Backend (Recommended)**: [`vercel-labs/agent-desktop`](https://github.com/vercel-labs/agent-desktop) for fast, headless background Accessibility control:
  ```bash
  npm install -g agent-desktop
  ```
- **Tier 3 Backend (Optional)**:
  - **Codex App**: Includes native Computer Use out of the box when `ChatGPT.app` is installed.
  - **Claude Code / Google Antigravity / Codex CLI**: Optionally install [`amontlabs/lcu`](https://github.com/amontlabs/lcu) (`v0.14.0+`) so every agent shares the local `ChatGPT.app` Computer Use engine.
- **PATH Check**: Ensure `~/.local/bin` appears **before** global npm/Homebrew bins in your `PATH` so CLI calls (including Codex shell commands) pass through the `agent-desktop` safety wrapper:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  ```

---

## 2. Clone & Install

Clone the repository anywhere on disk and run the idempotent installer:

```bash
git clone https://github.com/mcncarl/agent-desktop-router.git
cd agent-desktop-router

# Auto-detect installed agents (Claude Code, Codex, Antigravity)
# Use --lang zh if you prefer Simplified Chinese routing rules in AGENTS.md / CLAUDE.md
python3 scripts/install.py --agents auto
```

### Common Installer Flags

| Flag | Description |
| :--- | :--- |
| `--agents auto` | Auto-detect installed agents (`claude`, `codex`, `antigravity`). Pass `all` or comma-separated names to override. |
| `--lang en\|zh` | Language for the managed routing rules block (`en` default, `zh` for Simplified Chinese). |
| `--no-rules` | Install `PreToolUse` guards, CLI wrapper, and skills without touching `AGENTS.md` or `CLAUDE.md`. |
| `--dry-run` | Preview all planned changes without writing files. |
| `--json` | Emit structured JSON output for agent verification. |

### What `install.py` Wires Up

1. **Runtime & Native Window Inspector (`~/.local/share/agent-desktop-router/`)**:
   - Installs zero-dependency `src/guard.py` and compiles `src/probe.swift` → `src/probe` (`swiftc -O`) to inspect live window owners, PIDs, and screen coordinates.
2. **User Policy Config (`~/.config/agent-desktop-router/blocked-apps.json`)**:
   - Seeds the default protection list on first install (never overwrites your customizations on subsequent runs).
3. **CLI Safety Wrapper (`~/.local/bin/agent-desktop`)**:
   - If `agent-desktop` is installed, wraps it with `guard.py --cli-check` so **Codex** and direct terminal commands get the exact same window/PID/coordinate verification as hook-based agents.
4. **Host Integrations**:
   - **Claude Code**: Prepends `guard.py --protocol claude` to `hooks.PreToolUse` in `~/.claude/settings.json` (backing up your existing `settings.json` to `~/.claude/backups/`), installs `~/.claude/skills/desktop-browser-routing`, and appends the managed routing block to `~/.claude/CLAUDE.md` (or skips duplicating if `CLAUDE.md` already imports `~/.codex/AGENTS.md`).
   - **Codex**: Installs `~/.codex/skills/desktop-browser-routing` and updates the managed block in `~/.codex/AGENTS.md`.
   - **Google Antigravity**: Creates `~/.gemini/config/plugins/agent-desktop-router/` with `plugin.json`, `hooks.json` (`guard.py --protocol antigravity` + optional LCU lifecycle hooks), `rules/AGENTS.md`, `skills/desktop-browser-routing`, and `mcp_config.json` (when LCU `antigravity.mjs` is detected).

---

## 3. Run Health & Permission Diagnostics

```bash
python3 scripts/doctor.py
```

### Resolving Common Findings

1. **`Accessibility: denied`**:
   - Open **System Settings → Privacy & Security → Accessibility** and enable the host app running the agent (`Claude`, `Antigravity`, `ChatGPT`, or your terminal app such as `Ghostty` / `iTerm` / `Terminal`).
2. **`ChatGPT.app has unreadable files breaking LCU codesign check`**:
   - Some `ChatGPT.app` releases ship an internal `0600` file inside `Contents/Resources/` that causes non-root `codesign -vv` verification in `lcu` to fail. Run the exact `sudo chmod a+r ...` command printed by `doctor.py`.
3. **Activating in Google Antigravity**:
   - Restart Antigravity or reload plugins so `agent-desktop-router` loads its `PreToolUse` hook, rules, and skill.
4. **Approving Apps for Tier 3 (`LCU`)**:
   - When an agent escalates to Tier 3 Computer Use on a new app and no interactive prompt appears, approve it via Touch ID:
     ```bash
     lcu apps allow "App Name"
     ```
     *(Never approve apps that belong in your `blocked-apps.json`.)*

---

## 4. Customizing Protected Apps (`blocked-apps.json`)

Edit `~/.config/agent-desktop-router/blocked-apps.json` at any time — changes take effect immediately on the very next tool call without restarting your agent:

- `block_notification_center`: `true` blocks macOS Notification Center surfaces and banners so private message previews can never be read or clicked.
- `blocked_bundles`: Exact macOS bundle IDs (case-insensitive) blocked at process, `@snapshot:eN`, window, and `x,y` screen-coordinate levels.
- `blocked_patterns`: Case-insensitive regexes matched against CLI/MCP arguments and UI element metadata.
- `allowed_bundles` / `allowed_terms`: Explicit exemptions stripped before `blocked_patterns` matching (for example, allowing work apps like `com.tencent.WeWorkMac` / `企业微信` while keeping personal `com.tencent.xinWeChat` / `WeChat` strictly blocked).

---

## 5. Clean Uninstall

```bash
python3 scripts/uninstall.py
# Optional: also move ~/.config/agent-desktop-router to macOS Trash
python3 scripts/uninstall.py --purge-config
```

`uninstall.py` removes only `agent-desktop-router` hooks and `<!-- agent-desktop-router:start -->` rule blocks, and moves managed files to `~/.Trash` rather than permanently deleting files.
