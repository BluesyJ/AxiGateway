# AxiGateway

[![CI](https://github.com/BluesyJ/AxiGateway/actions/workflows/ci.yml/badge.svg)](https://github.com/BluesyJ/AxiGateway/actions/workflows/ci.yml)

AxiGateway 是运行在本机的 AI 流量监测网关。LiteLLM 负责模型转发，AxiGateway 负责事件采集、SQLite 存储和只读控制台。

## 功能

- OpenAI 兼容 Chat Completions 接入，支持普通响应和 SSE。
- 查看请求结果、耗时、token、缓存用量及费用估算。
- 按时间、模型、客户端标签和用量来源查询、汇总。
- 记录采集丢弃、写入失败和数据缺口。
- 使用独立演示库浏览界面，使用假上游测试转发链路。

当前支持 Windows x64、Python 3.13，固定 LiteLLM 1.106.5。代理监听 `127.0.0.1:4000`，控制台监听 `127.0.0.1:8788`。

```text
客户端 → LiteLLM Proxy → 模型供应商
              │
           事件回调 → 有界队列 → SQLite → 只读控制台
```

## 开始使用

按 [Windows 安装说明](docs/SETUP.md) 准备环境。安装完成后，可用合成数据体验控制台：

```powershell
.\.venv-litellm\Scripts\python.exe -I -S scripts\make_demo.py
.\.venv\Scripts\python.exe -m p3_console.server
```

打开 <http://127.0.0.1:8788/?source=demo>。演示数据在 `data/demo/`，无需模型 Key。

接入真实上游使用 `scripts/start-prod.ps1`，按提示输入 Key。客户端配置和停止方法见 [SETUP](docs/SETUP.md)。

## 当前进展

网关、事件库和控制台已实现，QQ 主聊天已有网关实测记录。当前工作是补齐备用模型与独立插件的接入验证，再开展供应商用量对账。具体阶段见 [ROADMAP](docs/ROADMAP.md)。

系统保存调用元数据，正文记录尚未实现。费用显示为“估算（非账单）”；未知值保留为空。控制台限本机访问，登录管理列入后续计划。

## 开发与协作

从 [START_HERE](START_HERE.md) 了解工程，从 [CONTRIBUTING](CONTRIBUTING.md) 了解分支、提交和 PR 流程。

| 内容 | 入口 |
|---|---|
| 架构与模块 | [工程说明](docs/ENGINEERING_HANDOFF.md) |
| 产品目标和客户需求整理 | [立项草案](docs/PROJECT_PROPOSAL.md) |
| 已批准的技术约束 | [SPEC](SPEC.md) |
| 核心测试 | [TESTING](docs/TESTING.md) |
| 安全问题与凭据处理 | [SECURITY](SECURITY.md) |
| 最近工作记录 | [PROGRESS](docs/PROGRESS.md) |

## 目录

```text
axi_gateway/       网关启动、事件采集和存储
p3_console/        只读查询服务
console_ui/        React 控制台
config/            配置模板
scripts/           启动、演示、对账和检查工具
tests*/            核心测试与假上游
assets/            tokenizer 数据
supply-chain/      固定运行环境清单
docs/              设计、路线图、操作说明和来源记录
```

首个提交从 2026-11-19 的完整工作区整理，原工程与历史记录保存在原目录。来源清单见 [SNAPSHOT](docs/SNAPSHOT.md)。本仓库用于私有协作；公开发布前由项目负责人选择许可证并完成第三方资产审查。
