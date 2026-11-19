# 开发协作

## 开始工作

先读 `START_HERE.md`、`AGENTS.md`、`SPEC.md` 和 `docs/ROADMAP.md`。在任务中写明目标、涉及模块、验收条件和验证预算。客户建议与产品决策分别记录，设计方案批准后进入实现。

新需求放在 `docs/proposals/`，或使用 Feature request 模板。写清使用场景、当前行为、期望结果及优先级。需求对齐 PR 可以只修改文档。

## 分支与提交

`main` 保存合并后的工程。任务使用短期分支：`feat/<topic>`、`fix/<topic>`、`docs/<topic>` 或 `chore/<topic>`。

```powershell
git switch main
git pull --ff-only
git switch -c docs/customer-requirements
```

一次提交围绕一个具体变化，标题建议使用 `feat:`、`fix:`、`docs:`、`test:` 或 `chore:`。例如：`docs: 补充客户接入场景和验收条件`。

换电脑继续工作前提交并推送分支，另一台拉取同一分支。两台电脑并行开发时按任务分支，避免同时修改同一组文件。

## Pull request

填写变更原因、实现内容、验证命令及结果。涉及界面时附合成数据截图；涉及数据结构、依赖或阶段范围时说明兼容性和迁移安排。使用 Squash merge 合并，合并后删除任务分支。

CI 运行文件检查、Ruff、后端核心测试、假上游契约、前端构建及浏览器测试。修复失败后再合并。当前只有一个维护者，CODEOWNERS 用于标识责任人，不要求同一账号批准自己的 PR。

当前账号套餐不支持私有仓库分支保护（2026-11-19 API 返回 403）。请通过 PR 合并，避免直接推送 main；升级到支持该功能的套餐后，可强制启用 PR、`windows-core` 检查及禁止强推/删除。

测试命令见 [TESTING](docs/TESTING.md)。自动测试全部使用合成凭据与假上游，禁止在 CI 中配置真实模型 Key。LiteLLM 升级按照 AGENTS 的公告核对、批准、哈希锁和契约测试流程执行。

## 文档维护

| 文档 | 更新内容 |
|---|---|
| SPEC | 已批准的行为与技术约束 |
| ROADMAP | 阶段定义和完成状态 |
| PROGRESS | 本轮结果、验证、问题和下一步 |
| ENGINEERING_HANDOFF | 模块、启动方式和环境恢复 |
| PROJECT_PROPOSAL | 产品目标和方案建议 |

日常迭代通过 Git 记录版本。`SNAPSHOT_MANIFEST.json` 保存首次协作快照，后续 PR 不覆盖它；文件增删和修改由提交及 PR 展示。

## 仓库内容

提交源码、配置模板、依赖锁和合成测试。`.env`、Key、真实数据库、日志、客户原始资料及运行环境留在本机。提交前执行 `git diff --cached --check` 和 `python scripts/check_repository.py`。

本仓库为私有协作项目。公开发布前安排许可证、第三方资产和历史内容审核。
