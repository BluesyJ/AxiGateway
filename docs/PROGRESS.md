# P6 控制台登录 / P7 模型与 Key 管理 — 内置 Playground

日期：2026-11-23。PR #2 经用户批准已合入 main `df21f075`；本轮按用户选择完成内置 Playground，分支 `feat/access-playground`，待审核。

1. 让操作员亲手完成“选应用/模型→调用→修改权限/RPM→停用→再次调用”，判断现有访问管理是否可用于演示。
2. 通过后交付本机入口和 PR；失败则记录具体阻碍，停止扩展到 Agent 工作台。
3. 上限：1 轮实现、1 次修复、10 个新增后端逻辑用例、6 个浏览器场景、30 次允许假调用、20 次拒绝探测、原 16 契约各一次、1 个工作日。真实调用 0。

## 完成了什么

已交付调用试验、应用权限、调用记录、治理审计四页，操作接入原管理 API 与 LiteLLM，支持授权、RPM、停用及刷新读取历史。UI 默认 `127.0.0.1:8790`；LiteLLM/假上游使用临时回环端口；4000、8787、8788、8789 保持原用途。每次启动创建独立 `.cache/playground/<run>/demo/`。启动见 [SETUP §6](SETUP.md#6-内置-playground-手动试用)，实现依据见 [计划](proposals/20261123-playground.md)。

## 改动的文件

新增 `axi_gateway/playground/`、`scripts/start_playground.py`、`tests_p7/test_playground.py`、`console_ui/playground.html`、`src/playground.{tsx,css}`、Playwright 配置与用例；修改 Vite 多入口、CI 和启动/交接文档。`launcher.py` 仅将离线 access-demo 的 master key 改为内存生成。复用现有组件，无依赖或哈希锁变更。

## 如何验证

本机核心 94 项、原 16 契约及允许表、前端构建/类型检查、既有浏览器 32 项、新增浏览器 6 项、Ruff 均通过。新增后端 4 个逻辑用例；四页深色正常/空/错误、加载与窄屏截图已核验。Playground 本机累计 6 次允许假调用、6 次拒绝；原契约另计，真实调用 0。结论见 [结果](PLAYGROUND_RESULT.json)。

一次独立复核发现演示 master key 落盘及浏览器断线误报 503，先复现再修复，两项回归已通过：凭据只在内存，断线保留发送时身份并显示结果未知。Windows 沙箱的临时目录/子进程限制通过正常权限完成验证；构建保留既有图标库提示，无依赖改动。11月20日 100 次假调用属于 [历史后端证据](P7_ACCESS_RESULT.json)。

## 待决问题

正式企业登录、凭据发放/轮换、Token/费用账本和 IdP 后续。旧 P4 待办、项目许可证保持原状态。旧 `start_access.py` 吞掉启动原因、旧 RPM 演示直接修改计数的审查意见尚未修复；本轮新入口提供受控错误和真实请求限流演示。

## 偏离 SPEC 的地方 / 不做的事

本机演示操作员可控制合成应用，页面不提供生产管理认证；JWT 不进入浏览器。无真实调用、无 enterprise/prisma、无正文留存、无 Agent/MCP/工具广场、无依赖变更、无自动合并。另一台开发前同步 main 并检查开放 PR。
