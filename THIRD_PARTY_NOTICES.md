# Third-Party Notices & Ecosystem Credits

`agent-desktop-router` does **not** bundle or redistribute code from third-party automation runtimes. Instead, it acts as a cross-agent routing layer, policy configuration, and `PreToolUse` / CLI safety guard that integrates with the following open-source tools installed separately by the user:

1. **`agent-desktop` (`vercel-labs/agent-desktop`)**
   - Repository: <https://github.com/vercel-labs/agent-desktop>
   - License: Apache License 2.0
   - Role: Tier 2 macOS Accessibility tree inspection and headless background UI actions.

2. **`lcu` (`amontlabs/lcu`)**
   - Repository: <https://github.com/amontlabs/lcu>
   - License: MIT License
   - Role: Optional Tier 3 Computer Use MCP bridge allowing Claude Code, Google Antigravity, Codex CLI, and Pi to share the local `ChatGPT.app` Computer Use engine.
