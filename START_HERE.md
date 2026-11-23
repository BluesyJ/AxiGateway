# 接手入口

当前产品方向见 [ItbigtecGateway V3.3](docs/PROJECT_PROPOSAL.md)。先读 [目标对齐与任务交接](docs/proposals/20261120-enterprise-governance-v33.md)，再读 [README](README.md)、[工程说明](docs/ENGINEERING_HANDOFF.md)、[ROADMAP](docs/ROADMAP.md)、[SPEC](SPEC.md)、[AGENTS](AGENTS.md)。

项目建设模型、MCP、Skill与Agent的统一接入及治理系统；六个产品版本对应最小系统、模型访问管理、工具广场、企业治理演示、完整方案演示和可安装试用版。产品版本与原有P阶段编号分别管理，工程状态以ROADMAP为准。

当前任务是 [P6/P7 模型访问管理后端](docs/proposals/20261120-model-access.md)，用户已授权当前机器规划并直接开发，完成后提交 PR。用户验收并决定合并；另一台机器开发前同步 main、检查开放 PR 和未提交改动。双方仍可承担设计、开发和复核，无需逐项跨聊天认领。

仓库从独立源码快照建立，原工程历史保留原位。来源与历史验证见 [SNAPSHOT](docs/SNAPSHOT.md)，分支与PR流程见 [CONTRIBUTING](CONTRIBUTING.md)。接手前检查最新提交、开放PR和本机未提交工作。所有新验证默认使用假上游，历史真实调用授权不延续。
