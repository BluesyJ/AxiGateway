# 当前工程说明

2026-11-23追加：用户选择OpenAI兼容接口，自有模型与连续对话继续放入PR #3。新增`playground/models.py`管理DPAPI配置，`model_runtime.py`按选择运行原LiteLLM master模式，`model_api.py`提供同源接口；前端`src/models.tsx`显示「我的模型」「模型对话」。数据默认`data/playground/`，不与每次重建的demo库混合。运行和隐私边界见[SETUP §7](SETUP.md#7-添加自有模型并对话)。`--offline-models`为验证入口约束，不放宽旧demo身份认证限制。页面保存后才可手动发送，保存不进行上游探测；测试脚本只使用合成配置。

2026-11-23：PR #2 经用户批准已合入 main `df21f075`。本轮新增 P6/P7 内置 Playground，代码待 PR 审核；入口 `scripts/start_playground.py`，默认 `127.0.0.1:8790`，使用原锁环境运行。`axi_gateway/playground/runtime.py` 管理合成身份、假上游与 LiteLLM 子进程，`server.py` 将同源页面请求接入既有管理 API 和模型网关。前端入口 `console_ui/playground.html`，构建与原只读控制台共用锁文件。启动及操作见 [SETUP §6](SETUP.md#6-内置-playground-手动试用)，验证见 [结果](PLAYGROUND_RESULT.json)。

每次运行建立独立 `.cache/playground/<run>/demo/`，刷新保留当前策略、审计和事件，重启新建 demo。私钥、token 和离线 access-demo 的 master key 只在内存；普通 master 模式继续用原 DPAPI 流程。本机操作员可管理合成应用，没有正式企业登录。页面仅显示固定假回复与元数据，无真实流量、正文留存或 Agent 执行。另一台开发前同步 main，检查开放 PR 和本机未提交改动。

2026-11-20 新增离线模型访问管理后端，交付与限制见 [开发计划](proposals/20261120-model-access.md) 和 [SETUP §5](SETUP.md#5-模型访问管理后端演示)。`axi_gateway/access/identity.py` 复用 PyJWT 验证外部短期签名身份，`hook.py` 接入原 LiteLLM OSS 扩展，`policy.py` 在独立 demo 数据库执行授权、RPM 与治理审计，`server.py` 提供独立管理 API。`scripts/start_access.py` 默认绑定 8789；演示脚本使用空闲端口。

4000 允许表和 8788 只读服务未扩展管理路由；签名身份模式必须显式离线启用。事件表没有迁移，`key_alias` 仅在此模式取框架可信应用标识。治理数据库不保存凭据，管理成功返回前提交策略和审计；调用事件仍尽力采集。公钥与管理员信任配置重启生效；登录页面、正式 IdP、Key 发放、Token/费用额度后续开发。旧源码快照和原机私有运行资料没有迁入当前克隆。

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

截至原 2026-11-12 资料，正文与错误详情、界面简化、并发与延迟验证、易用部署是用户提出的需求；存储容量、保留期、阶段归属尚未批准。最新产品目标已整理为 [V3.3](PROJECT_PROPOSAL.md)；当前机器直接开发、用户审核 PR，另一台开发前同步 main。原客户端未完成项延期，不因当前开发改写历史状态。

## 使用与开发

[SETUP](SETUP.md) 包含安装、演示、生产入口；[TESTING](TESTING.md) 列核心验证范围。生产与测试分别使用数据目录，不附带真实凭据、DPAPI、事件库或真实调用额度。升级仍须核对公告、用户批准、新哈希锁及完整兼容性验收；这个精简测试集不代表历史全部验收。
