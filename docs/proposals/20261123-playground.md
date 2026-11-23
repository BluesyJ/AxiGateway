# P6 / P7 内置 Playground 实施计划

2026-11-23；基线 main `df21f075`。用户选择内置 Playground：选择应用和模型、手动调用、调整授权/RPM/停用、查看结果。

## 设计与范围

沿用 React/TypeScript/Vite/Tailwind 和现有 Card/Button，新增独立 `playground.html`。页面包含调用、应用权限、调用记录、治理审计四个视图，提供加载、空状态和错误状态。默认 `127.0.0.1:8790`；8788 保持只读，8789 管理 API 保持原行为，4000/8787 不占用。页面明确标为离线演示，假上游返回固定示例，输入正文只在本次请求内使用。

演示启动器创建 `.cache/playground/<run>/demo/`，启动临时回环端口的 LiteLLM 和假上游。UI 服务通过已有管理 ASGI 应用执行授权修改，通过 LiteLLM 发起模型请求。签名私钥与短期凭证仅在服务端内存；浏览器不收取 JWT。Host/Origin 严格检查，写请求必须携带同源 Origin 和自定义请求头，CSP 禁止跨域连接。本机可访问服务的操作员拥有这份合成数据的演示控制权；本阶段没有企业登录或生产管理认证。

策略、审计和 P2 元数据分别存储；刷新读取当前演示库。请求/响应正文不入日志或数据库，响应面板仅展示固定假回复。每次启动建立新演示环境，不覆盖历史 demo 或实际库；不提供任意上游地址、数据库路径或真实 Key 输入。

## 复用与取舍

已阅读 [TrueForge](https://github.com/truefoundry/trueforge) README、MIT 许可证和 Quickstart（Node >=22.14；本地 SQLite，托管模式 Postgres/Redis），以及 [Deep Agents](https://github.com/langchain-ai/deepagents) README、MIT 许可证和安装说明（Python/LangGraph，可配置持久化与工具）。二者提供 Agent 执行循环，超出本次手动试用范围。本次复用原 UI 与锁内 httpx/Starlette/PyJWT；不新增包。TrueFoundry Playground 的应用/模型选择与结果展示作为交互参考，不复制其代码。

## 文件与任务

1. `axi_gateway/playground/runtime.py`、`scripts/start_playground.py`：创建合成环境、签发内存凭证、管理子进程生命周期、输出受控启动错误。`axi_gateway/launcher.py` 的离线 access-demo 分支使用一次性内存 master key，普通模式保持原行为。
2. `axi_gateway/playground/server.py`：同源 API、固定管理/模型桥接、只读调用记录；`tests_p7/test_playground.py` 先证明缺少接口，再覆盖隔离、权限修改、输入与错误语义。
3. `console_ui/playground.html`、`src/playground.tsx`、`src/playground.css`、`vite.config.ts`：独立页面与原组件复用，不修改现有只读页面行为。
4. `console_ui/playground.config.ts`、`playground-tests/playground.spec.ts`：真实网关/假上游的浏览器操作与截图；既有浏览器测试仍单独执行。
5. `docs/SETUP.md`、`docs/PROGRESS.md`、`docs/ROADMAP.md`、`docs/ENGINEERING_HANDOFF.md`、CI：启动说明、验收结论与新入口验证。

执行采用本会话实现（writing-plans / executing-plans），复用当前干净独立克隆的新分支 `feat/access-playground`；无需再恢复一份锁环境。各任务共享接口为 GET `/api/demo/state`、PUT `/api/demo/apps/{id}`、POST `/api/demo/call`、GET `/api/demo/events`。调用返回明确 HTTP 状态与受控类别，不把失败变成成功空结果。

## 验证预算与验收

- 1 轮实现、1 次修复、1 个工作日。新增后端最多 10 个逻辑用例、浏览器最多 6 个场景；手动/浏览器假上游允许调用合计最多 30 次，拒绝探测最多 20 次；真实调用 0。
- 新增测试先失败后通过；最终后端核心、原 16 契约、允许表、Ruff、前端构建/类型检查、既有前端测试各一次。变化或失败仅复测受影响项，不扩展调查。
- 重点覆盖：未授权跨站写入、过期页面并发覆盖、停用后旧身份、RPM 真实请求累加、模型服务中断和 NULL 显示。UI 深色截图覆盖四个视图、空/错误及窄屏；原始截图仅留忽略目录。
- 成功后留下可操作本机入口并提交 PR，用户决定合并；失败或预算耗尽记录具体阻碍。

## 不做的事

不接真实模型、企业 IdP、Agent/MCP 执行、凭据产品化、价格/Token 预算、正文留存或任意网络代理；不改 legacy、8787、AstrBot、依赖锁；不自动合并。旧启动器诊断和旧 RPM 自动演示问题保留为历史待办，不顺带扩展修复。
