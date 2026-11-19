# 当前工程说明

本页为 2026-11-19 协作快照导航；阶段状态只以 [ROADMAP](ROADMAP.md) 为准。源码来自原工作区，不等于原 HEAD；详见 [来源](SNAPSHOT.md)。

## 当前系统

```text
OpenAI 兼容客户端
  → 127.0.0.1:4000 / LiteLLM Proxy
    → 路径与方法允许表 → LiteLLM 鉴权 → 模型供应商
    → logging 回调 → 白名单投影 → 有界队列 → 单写线程 → SQLite WAL
                                                          ↓ 只读
                                          127.0.0.1:8788 / 运维控制台
```

代理环境限定 Windows x64 / CPython 3.13、LiteLLM 1.106.5 与原 117 项哈希锁。普通用户态启动，不自研转发器、认证或虚拟 Key。DeepSeek 双模型模板暴露 `deepseek-v4-pro` 与 `deepseek-flash`；其他供应商能力尚未验收。

`axi_gateway/launcher.py` 生成运行配置并启动原版 Proxy；`bootstrap.py` 校验依赖与 tokenizer；`exposure.py` 仅放行批准路径。`callbacks.py/events.py` 做回调和字段投影，`collector.py/storage.py` 异步批量写库，`queries.py` 提供只读查询。`p3_console` 服务六视图，`console_ui` 是当前深色 React 界面。

## 数据与限制

- SQLite 含 events/gaps/counters，调用 ID 幂等，尽力投递且记录丢失，不承诺零丢失。转发前拒绝只计数。
- 只存元数据，**没有请求/回复/reasoning 正文及详细错误文本**；`LOG_CONTENT=true` 会被拒绝。
- 用量按 reported/derived/estimated/missing/unknown 分开；NULL 不补零。固定版本非流式成功的合法正用量按 D03 记 reported。流式通常 unknown，可信断开记 estimated。
- reasoning 属于 completion 子集。费用存金额、币种、来源、价目版本；为估算而非账单。峰谷价格和历史原因限制沿用原项目结论，真实对账未完成。
- 起止时间存 UTC；无时区回调按主机事件时刻的当地时区解释，界面显示北京时间；历史错时记录未回填。
- 控制台只 GET、SQLite 只读，有 Host/Origin/CSP 防护，尚无登录。回环不等于认证。代理无钥匙不得调用模型，但 401 状态码仍有已批准偏差。

默认实际库 `data/p1/events/events.sqlite3`，演示库 `data/demo/demo-events.sqlite3`。演示数据有明确标识；没有实际库时会显示读取错误，当前尚未实现“库缺失也显示空库”的改动。

## 业务状态与新需求

主聊天通过来自负责人此前 QQ 实测；备用与阅读插件缺独立实测，记忆 LLM 上次配置仍直连。副本不包含 AstrBot 或其配置，本轮不重新核实实际流量。

截至原 2026-11-12 资料，正文与错误详情、界面简化、并发与延迟验证、易用部署是用户提出的需求；存储容量、保留期、阶段归属尚未批准。本次新增客户方案不在这台电脑的已确认资料中，应由另一台形成需求对齐提案后提交负责人决定。见 [PROJECT_PROPOSAL](PROJECT_PROPOSAL.md)。

## 使用与开发

[SETUP](SETUP.md) 包含安装、演示、生产入口；[TESTING](TESTING.md) 列核心验证范围。生产与测试分别使用数据目录，不附带真实凭据、DPAPI、事件库或真实调用额度。升级仍须核对公告、用户批准、新哈希锁及完整兼容性验收；这个精简测试集不代表历史全部验收。
