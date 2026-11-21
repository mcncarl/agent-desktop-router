<!-- agent-desktop-router:start -->
## 桌面与浏览器三层自动化路由

按“**能调接口不点界面，能读结构不看截图**”从上往下选工具，上一层够不到再切下一层：

1. **第一层：原生接口（优先，不动界面）**
   - 脚本、CLI、API 或直接读写本地文件（如 ExtendScript、工程草稿文件、本地 Markdown、官方 CLI）。
2. **第二层：结构化后台控制（内置浏览器 + `agent-desktop`）**
   - **免登录外部网页**：优先走 Agent 自带的内置浏览器。
   - **需复用日常 Chrome 登录态，或操作结构公开的桌面软件（Obsidian 等 Electron/原生应用、各软件顶部菜单栏）**：使用 `agent-desktop` 默认后台模式（不动鼠标、不抢焦点）。操作真实 Chrome 时单开专用窗口并用 `--window-id` 锁定，避免用户切标签页冲掉元素引用。
   - 在命令行直接按字面量传 `--app`、`--window-id`、`@snapshot:eN` 引用或 `--xy` 坐标，不包进脚本或用变量；仅当后台点击无效时才用 `--headed` / `mouse-click`，用前先告知用户并确认目标窗口在最上层。
3. **第三层：截图视觉兜底（Computer Use / `LCU`）**
   - 仅在自绘界面读不到元素、按钮后台点不动、操作 iPhone 镜像或需肉眼核对版面视觉效果时，切到 Computer Use（Codex 原生 CUA 或 `LCU` MCP）。
4. **安全禁区边界**
   - 严禁通过任何通道触碰、读取或截图 `~/.config/agent-desktop-router/blocked-apps.json` 中的受保护应用及系统通知中心。
<!-- agent-desktop-router:end -->
