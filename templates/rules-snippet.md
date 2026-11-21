<!-- agent-desktop-router:start -->
## Desktop & Browser Automation Routing

Follow "prefer native APIs over UI clicks, and structured trees over screenshots" from top to bottom, escalating only when the upper tier cannot complete the step:

1. **Tier 1 — Native Interfaces (No UI Automation)**
   - Use scripts, CLIs, or direct file edits first (e.g., ExtendScript, project/draft files, local Markdown edits, official CLIs/APIs).
2. **Tier 2 — Structured Background Control (Built-in Browser & `agent-desktop`)**
   - **Web pages (route by host cookie sync)**: If the host's built-in browser already shares/syncs your Chrome login state (**Codex** / **Claude Code**), or if the page requires no login (**Antigravity** built-in `browser`), use the **built-in browser** first. When the host's built-in browser is an isolated sandbox without your login state (**Antigravity** on logged-in sites, or pure terminal CLI sessions), use `agent-desktop` on real Chrome with a dedicated window locked via `--window-id` so tab switches in your personal window never invalidate `@snapshot:eN` refs.
   - **Accessible desktop apps (Electron/native apps, menu bars)**: Use `agent-desktop` in headless background mode (does not move the mouse or steal window focus).
   - Pass literal values for `--app`, `--window-id`, `@snapshot:eN` refs, and `--xy` coordinates directly on the command line (no wrapper scripts or shell variables). Use `--headed` / `mouse-click` only when headless actions have no effect — inform the user first and verify the target window is frontmost.
3. **Tier 3 — Visual Fallback (Computer Use / `LCU`)**
   - Escalate to screenshot-based Computer Use (`LCU` MCP or native CUA) only when accessibility trees are empty (custom-drawn canvases), headless clicks do not register, iPhone Mirroring is needed, or visual layout/color verification is required.
4. **Hard Safety Boundary**
   - Never inspect, focus, click, type into, or screenshot apps listed in `~/.config/agent-desktop-router/blocked-apps.json` or macOS Notification Center across any channel.
<!-- agent-desktop-router:end -->
