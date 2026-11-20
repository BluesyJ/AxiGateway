# 核心验证

所有命令从本副本运行。仅合成数据，禁止非回环模型网络；产物在忽略目录内。生产业务源码没有因裁剪改动。

| 验证 | 命令或入口 | 覆盖 |
|---|---|---|
| 仓库检查 | `python scripts/check_repository.py`、`python scripts/test_repository_checks.py` | 暂存文件排除、凭据模式、文档链接与依赖资产 |
| 核心后端 | `.venv\Scripts\python.exe -B scripts\test_core.py` | 隐私、来源、白名单、SQLite 幂等/失败/强杀、查询安全、对账、价格边界 |
| 真 Proxy + 假上游 | 同上加 `--contracts` | 原 16 场景、压缩、断开、默认/显式关闭正文与库级隐私 |
| 模型访问管理演示 | `.venv-litellm\Scripts\python.exe -I -S scripts\demo_access.py` | 2 模型 × 2 应用、100 次允许假调用、身份伪造/越权/RPM/停用、独立审计及隐私；无真实流量 |
| 前端 | `console_ui` 下 `npm.cmd run typecheck`、`npm.cmd run build`、`npm.cmd test` | 沿用 P3.1 桌面状态、筛选、分页、窄屏与只读隔离断言 |
| 静态检查 | `.venv\Scripts\python.exe -m ruff check .` 与 `ruff format --check .` | Python 规范与格式 |

后端统一入口先校验代理环境，再将已校验运行包路径作为低优先级补充，用于开发锁未包含的 PyYAML；不执行该环境 .pth。pytest 使用开发环境，不能向严格代理环境安装测试工具。

前端测试默认新建 `.cache/p31/run-*` 合成库并用 18789 端口；若端口被占用则停止，不复用现有服务。首次需本机 Edge（当前测试锁使用 msedge channel）。可通过 `AXI_TEST_PYTHON` 指定已有开发解释器；它只是本机验证工具，不进入源码清单。所有截图保留在 `.cache`，不复制历史截图。

仓库保留核心测试，阶段编排、旧全路由盘点、真实冒烟和一次性剪贴板诊断留在原工程。升级 LiteLLM 时按 AGENTS 执行完整兼容性契约，并从原工程恢复相关专项工具。

GitHub Actions 在 Windows 上执行同一套检查；PR 合并前查看 `windows-core` 结果。测试无需配置 GitHub Secrets，不能向工作流添加真实模型 Key。原快照的 `SNAPSHOT_MANIFEST.json` 和 `verify_snapshot.py` 用于核对整理时的文件，后续提交会与该历史基线产生差异；日常提交使用上表的仓库检查。Git 统一文本换行，哈希锁和 tokenizer 资产保持原字节。

`tests_p7/test_access.py` 纳入核心回归，覆盖短期签名校验、独立 audience、模型权限、并发 RPM、重启窗口、策略版本冲突、审计失败回滚、Host/Origin/重复头/请求体上限与凭据哨兵。治理状态和事件库为不同数据库。演示进程结果只保存结论；私钥、token 和正文不落盘。没有修改前端的增量只执行相关后端检查，本地不重复截图；CI 保留既有前端回归。
