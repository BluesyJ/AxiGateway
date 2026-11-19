# 工程进度 — 私有仓库初始化

1. 本轮交付可供跨电脑协作的 GitHub 私有仓库，统一代码、需求提案和 PR 入口。
2. 检查通过后上传；认证或仓库设置受限时记录具体限制，保留本地交付。
3. 上限：2 轮、4 组检查、1 个工作日；检查文档与文件卫生、本地校验、远端提交和 CI/仓库设置，第二轮只修正失败项。

范围：README/协作与安全说明、Issue/PR 模板、CODEOWNERS、GitHub Actions、仓库文件检查脚本、进度与来源清单。上传 AxiGateway-clean；原 AxiGateway 与历史标签保持原位。沿用现有依赖、业务实现与测试结果，模型调用 0 次。使用私有仓库，暂不选择开源许可证。

历史整理记录见 archive/PROGRESS_20261119_SNAPSHOT.md。本轮用户已授权提交和上传。

## 完成了什么

创建 [BluesyJ/AxiGateway](https://github.com/BluesyJ/AxiGateway) 私有仓库，默认分支 main。整理首页、安装说明、贡献流程、安全说明、需求与缺陷模板、PR 模板和 CODEOWNERS。新增 Windows CI：文件卫生、Ruff、后端测试、假上游契约、前端构建及浏览器测试。全部 Actions 固定到提交 SHA，工作流权限为只读，无真实模型 Secrets。

## 改动的文件

README、AGENTS、START_HERE、CONTRIBUTING、SECURITY、docs/SETUP、docs/TESTING、docs/proposals/README、.github、.editorconfig、.gitattributes、scripts/check_repository.py 与对应测试。业务源码、配置模板、依赖版本和哈希锁保持原样。Git 中普通文本统一 LF，锁与 tokenizer 资产保留原字节。

## 如何验证

本轮本地完成仓库检查、3 项检查脚本测试、Ruff check/format 和 Git 空白检查。完整系统测试沿用整理记录：77 个后端用例、16 个假上游场景、32 个浏览器用例通过；首次云端执行结果见 [Actions](https://github.com/BluesyJ/AxiGateway/actions/workflows/ci.yml)。原快照证据保持原样，后续来源由 Git 提交追溯。

## 待决问题

另一台电脑将客户建议整理为 docs/proposals/ 下的需求提案，通过 PR 讨论；产品阶段和范围沿用 ROADMAP。公开发布前确定许可证，新增功能按提案逐项批准。

## 偏离 SPEC 的地方 / 不做的事

原工程及历史资料保留原位。本轮不改业务规则，不接入真实模型，不操作 AstrBot 或原机服务，不上传数据、凭据和历史截图，不公开仓库、不发布版本。
