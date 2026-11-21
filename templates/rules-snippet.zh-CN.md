<!-- agent-desktop-router:start -->
## 桌面与浏览器三层自动化路由

按“**能调接口不点界面，能读结构不看截图**”从上往下选工具，上一层够不到再切下一层：

1. **第一层：原生接口（优先，不动界面）**
   - 脚本、CLI、API 或直接读写本地文件（如 ExtendScript、工程草稿文件、本地 Markdown、官方 CLI）。
2. **第二层：结构化后台控制（内置浏览器 + `agent-desktop`）**
   - **浏览器网页（按宿主是否已同步 Cookie 分流）**：若当前宿主内置浏览器已与日常 Chrome 同步登录态（如 **Codex**、**Claude Code**），或访问免登录公开页面（含 **Antigravity** 内置 `browser`），一律优先走**内置浏览器**；若宿主内置浏览器为独立沙盒无登录态（如 **Antigravity** 操作已登录站点，或纯 CLI 终端无内置浏览器），则直接用 `agent-desktop` 在真实 Chrome 单开专用窗口并用 `--window-id` 锁定后台操作，避免用户切标签页冲掉元素引用。
   - **结构公开的桌面软件（Obsidian、企业微信等 Electron/原生应用及顶部菜单栏）**：统一使用 `agent-desktop` 默认后台模式（不动鼠标、不抢焦点）。
   - 在命令行直接按字面量传 `--app`、`--window-id`、`@snapshot:eN` 引用或 `--xy` 坐标，不包进脚本或用变量；仅当后台点击无效时才用 `--headed` / `mouse-click`，用前先告知用户并确认目标窗口在最上层。
3. **第三层：截图视觉兜底（Computer Use / `LCU`）**
   - 仅在自绘界面读不到元素、按钮后台点不动、操作 iPhone 镜像或需肉眼核对版面视觉效果时，切到 Computer Use（Codex 原生 CUA 或 `LCU` MCP）。
4. **安全禁区边界**
   - 严禁通过任何通道触碰、读取或截图 `~/.config/agent-desktop-router/blocked-apps.json` 中的受保护应用及系统通知中心。
<!-- agent-desktop-router:end -->
