# AxiGateway — SPEC v0.2

## 2026-11-23 开发增量：本机自有模型对话

用户授权在P7添加自有模型，并选择OpenAI兼容接口。按[实施计划](docs/proposals/20261123-local-models.md)新增本机模型管理和连续文字对话：模型配置及上游Key以Windows当前用户DPAPI加密保存，Key不回显、不进入日志或SQLite，启动Proxy时只通过子进程环境传递；正文仅用于当前请求/页面，不持久化。8790只绑定回环并保留同源检查，无企业登录。

真实模型测试使用原LiteLLM master模式，浏览器不获取master；原demo签名身份及授权演示仍限假上游，不放宽`--access-demo`的离线限制。原启动配置增加`openai/`模型前缀；DeepSeek配置、数据面允许表、D02–D08及401偏差保留。用户点击发送触发所选服务的请求；本轮助手与CI自动测试只用独立合成数据和假上游，真实调用0。配置管理不自动探测或调用模型，不新增依赖。

## 2026-11-20 开发增量：离线模型访问管理

用户授权本机规划并直接开发后提交 PR，由用户审核合并。P6/P7 首个增量采用 [模型访问管理计划](docs/proposals/20261120-model-access.md)，仅通过显式 `--access-demo`、`--offline` 和独立 demo 目录启用，普通启动仍执行下文 master 模式。

- D01 的扩展：LiteLLM OSS `custom_auth` 调用锁内 PyJWT，验证外部签发的 RS256 短期身份；固定公钥、issuer、独立 audience 和最大 15 分钟有效期。管理主体另有明确名单。网关不生成产品凭据、不存密码或私钥；脚本内存签名只用于测试。企业登录、凭据发放与轮换界面不在本增量。
- D09 的扩展：独立管理进程默认 `127.0.0.1:8789`；仅开放受认证的应用查询、策略替换与审计查询，Host/Origin 精确匹配、无跨域、CSP、响应不含凭据与正文。4000 的 ExposureGate 及 8788 的只读 API 保持原样。
- 应用授权、停用和 RPM 在独立 `demo-policy.sqlite3` 内执行；状态变更与审计同事务，存储失败拒绝操作。RPM 按 UTC 自然分钟计已准入请求，上游失败也消耗，模型列表不消耗，重启不重置当前窗口。自然分钟边界允许短时突发。没有 Token/费用预算；调用事件的 D07 尽力投递口径不变。
- 演示请求只允许 `access/protocol.py` 列明的标准 Chat Completions 字段；客户端不能通过请求级 fallback、重试、上游地址或凭据改变路由。管理启动器禁止使用 4000、8787、8788；冲突的演示与复制密钥选项在读取密钥前拒绝。
- 仅该模式下，事件 `key_alias` 取固定版 LiteLLM 清理并写入的 `user_api_key_alias`；不读原始请求自报身份。停用成功后拒绝新的准入，已经准入的请求继续，不宣称可撤回上游操作。

本增量验收不构成真实调用、正式身份部署、完整 P6/P7 或产品第二版本的验收。原 D02–D08、D10–D12 和 401 known_deviation 保留；项目许可证与正式企业登录方案属于后续交付。

> 协作快照说明（2026-11-19）：批准规则保持原意；文中 legacy、旧报告、原阶段授权等历史描述指原工程，原件不在此快照。当前阶段及状态以 docs/ROADMAP.md 为准。整理不授予真实调用或上传权限。

日期：2026-11-02。状态：**已批准；许可证、管理认证方式待定**。用户明确批准 D01–D12；D02/D03 选方案 A，D07 采用“尽力投递，丢失可见”。代理暂用 127.0.0.1:4000。该版本已提升为根 SPEC.md；提案路径保留批准记录。P1 已获实施授权，P2–P4 仍须逐阶段授权。

## 1. 定位与边界

AxiGateway 是通用开源的 AI 流量监测网关。LiteLLM Proxy 负责转发、供应商协议适配及其虚拟 Key 能力；AxiGateway 负责事件采集、SQLite 事件存储、统计和仪表盘。AstrBot/阿汐只是后续验证客户端，不写入核心领域模型或接口约束。

先做接入和监控。首个验证供应商为 DeepSeek Chat Completions，不宣称已经支持所有供应商。不得自研转发器或虚拟 Key，不 fork LiteLLM，不引用或复制 enterprise/ 目录代码，不安装 litellm-enterprise 发行包。SQLite 事件库不替代 LiteLLM 凭据数据库。

保留 legacy-m1-m1.1 基线、原 SPEC/AGENTS、旧 app、前端、测试和历史证据；不删除文件、不改 8787 服务。P1 只实现启动、接入和开发用事件采集；SQLite、控制台改接及 AstrBot 对账属于后续阶段。

## 2. 复用范围

- 前端 React + TypeScript + Tailwind + shadcn/ui + Recharts 控制台及已验收的深色运维规范；P3 改读事件库。
- 假上游、真实回环 TCP、SSE、上游错误、慢消费者和断开连接测试。
- 字段白名单、正文/凭据哨兵、日志脱敏经验。
- 回环监听、Host/Origin、CSP 经验，分别用于管理面验证；不假定旧防护自动覆盖 LiteLLM 数据面。

## 3. 已批准架构

```text
兼容客户端 → LiteLLM Proxy → 模型供应商
                  │ 白名单回调 / 非阻塞入队
                  v
             同进程有界队列
                  │ 单写线程
                  v
              SQLite（WAL）
                  │ 只读连接 / 查询 API
                  v
               既有控制台
```

不增加本地 HTTP 事件中转。队列满、写失败、掉电和进程崩溃的可靠性边界不能以架构图掩盖，见 D07。P0/P0.1 的 JSONL、临时端口及限额传输探针不是产品方案；不把实验 HTTPX 旁路探针自动纳入 P1。

## 4. D01–D12 修订清单

### D01 无数据库接入与鉴权 — 已批准

阶段一单个 master key，仅回环监听；这只是受控本地接入阶段，不向客户端分发管理员 Key 作为长期方案。分别保留 `client_label`（调用方自报，不可信，只供展示区分）和 `key_alias`（LiteLLM 鉴权产生的可信非敏感标识，可空）；不得用 Key 本身、Key 哈希或自报标签填充可信字段。

可信的按客户端归属留给后续可选 PostgreSQL + LiteLLM 虚拟 Key 模式。不自研虚拟 Key，不用 SQLite 事件库保存凭据。

### D02 客户端中断语义 — 已批准（P1.1 升级）

依据：2026-11-02 用户 P1.1 明确批准，以及 P1/P1.1 假上游中 SSE 首块前/中途断开的标准日志证据。仍只使用 LiteLLM logging 回调，不增加传输/迭代器旁路。

仅当 `stream=true` 且 LiteLLM 标准日志 error_information 中 error_code=499 或 error_class=ClientDisconnected，或清理后的 litellm_params.metadata.client_disconnected=true 时，记录 `client_outcome=client_disconnected`、`usage_source=estimated`。数值缺失为 NULL，不补零；缓存/reasoning 仍遵守 D04。该结果只表示 LiteLLM 观察到连接断开，不证明用户主动取消或供应商停止扣费。

非流式及没有标记的请求 client_outcome 保持 unknown；不从 success/failure 或异常文本猜测断开。只读框架标准日志/清理后元数据，不读原始请求正文的伪造标记；伪造 standard_logging_object/metadata 的契约必须通过。详见 P1.1 交付（原工程 `docs/P11_DELIVERY.md`，本快照未附）。

### D03 usage 来源 — 已批准

枚举 `reported / derived / estimated / missing / unknown`。无法区分时为 unknown；仪表盘分栏汇总，不混成一个总数。P4 与 DeepSeek 控制台对账只使用 reported。

2026-11-05 P4.0b 批准细化方案 A。仅在锁定 LiteLLM 1.106.5、非流式、供应商成功、客户端无断开标记，且有效 prompt_tokens 和 total_tokens 均大于 0 时，产品回调记 reported。依据是该版非流式缺 usage 的契约返回 0/0/0。该零值保留原分类，无 usage 对象仍为 missing。流式有 usage 时为 unknown，D02 确认断开为 estimated。升级重新验收，不使用客户端自报来源。

### D04 缓存与 reasoning — 已批准

reported 优先取合法的 DeepSeek 原始 prompt_cache_hit_tokens、prompt_cache_miss_tokens，实报 miss 标 reported。缺少原始字段时可取标准 cached_tokens；仅在 prompt 与 hit 合法且 hit ≤ prompt 时差值推导 miss 并标 derived。非 reported 的缓存细分和 reasoning 均为 NULL，缺失不补零。

reasoning 是 completion 的子集，不能重复累加进 total。P0.1 证实流式聚合保留标准 cached_tokens，但重建 Usage 时遗漏 DeepSeek 原始 hit/miss 扩展字段；无透明保留这些原名的现成 Proxy 回调配置。

### D05 费用来源 — 已批准

保存金额、币种、来源（`litellm_builtin / user_prices`）及价目版本或日期。界面标注“估算（非账单）”；未知为 NULL，不是 0。项目负责人填写自定义价格，禁止凭记忆填数。

P0.1 当前内置表包含已验证模型 deepseek-flash，实验记录表文件哈希和实际回调费用；该内置表可作为带版本的 litellm_builtin 估算来源，不是供应商账单。未来模型不在确认价目中时必须保持未知。

### D06 客户端标签 — 已批准

标签只作展示，无鉴权含义，可以伪造。header 与 metadata 冲突时两值都保留，不择一覆盖。可信 key_alias 与这两个自报值分别保存；缺少可信鉴权标识时留空。

### D07 回调事件传输与可靠性 — 已批准

可靠性口径为 **尽力投递，丢失可见**。回调白名单提取 → 同进程有界队列（put_nowait）→ 可替换 sink；队满/提取失败/写失败只计数、不抛异常、不阻塞请求。退出尽力排空，崩溃和存储不可用时可能丢失；指标持久化本身也可能失败，不保证持久交付。

P1 只提供明确标注“非产品”的开发 JSONL sink 和丢失计数，不做 SQLite。P2 使用单写线程 → SQLite WAL，控制台只读连接；litellm_call_id 幂等、缺口记录和仪表盘可见性在 P2/P3 验收。不增加本地 HTTP 中转。

### D08 请求/尝试/事件去重 — 已批准

一次调用一行，主键 litellm_call_id。失败以 async_log_failure_event 为准；Proxy post_call_failure hook 不单独入库，避免重复计数。fallback 和重试在 P2 前保持关闭。P1.1 已批准：框架标记为转发前拒绝的鉴权/凭据覆盖等失败不入事件 sink/库，只增加受控拒绝计数，不归类 provider_or_transport。P0.1 的多条探针事件是实验观察记录，不是产品行数。

### D09 数据面与管理面防护 — 已批准

控制台与管理 API 沿用回环监听、Host/Origin、CSP 经验并单独验证，回环不等于身份认证。2026-11-02 用户批准 P1.3 方案 B：同进程纯 ASGI「路径+方法允许表」，只开放 `POST /v1/chat/completions`、`GET /v1/models`，以及 `/health/liveliness`、`/health/liveness`、`/health/readiness` 的 GET/HEAD/OPTIONS。其余 HTTP 路径/方法及全部 WebSocket 握手，在进入 LiteLLM 处理前拒绝；不把 UI、SSO、公开目录批准为例外。

允许表只限制暴露，不认证、不读取/改写请求体、不缓冲响应、不吞取消、不改 Authorization 和被允许请求的响应头、不校验或生成 Key。LiteLLM 继续负责认证与转发。P1.3 安全验收为：无 Key 时不能调用模型、上游 0 次、无管理类副作用、允许表之外全部被拒。401 known_deviation 继续保存独立结果，不自写认证修复状态码；历史 P1.1/P1.2 失败证据不改。

### D10 旧入口与发布 — 已批准

8787 旧网关和 legacy 代码保持不动。新 LiteLLM Proxy 暂用 127.0.0.1:4000。P4 之前不修改 AstrBot 配置。当前不替换启动命令或迁移现网。

### D11 许可证与平台矩阵 — 已批准

许可证待定，发布前确定；建议 MIT 或 Apache-2.0，不替用户选择。P1 只承诺 Windows x64 + CPython 3.13，沿用现有精确版本哈希锁；其他平台单独生成锁并验证。

正式依赖集合排除 litellm-enterprise；启动时自检其未安装，并检查无异常 .pth/sitecustomize.py。合法 pywin32 启动钩子须与受信 wheel 哈希一致，不能把所有 .pth 都认作恶意，也不能不检查内容。

P0 探针转为每次 LiteLLM 升级必跑的兼容性契约测试。P1 启动器预置经哈希验证的 tokenizer 缓存、禁用远程价格表与遥测、生成并保管 master key。两版 Windows wheel 均存在 tokenizer CRLF 缓存问题，启动器需验证使用正确的静态资产，不能静默联网修复。

当前固定 `litellm==1.106.5`，117 项依赖全部精确固定并带 SHA-256。禁止范围依赖和自动升级，禁止 1.82.7、1.82.8、1.107.0。P0.1 推荐保留当前版本；任何更换仍须用户批准。

### D12 内容记录 — 已批准

P2 首版只存元数据，不实现原文记录。原文功能留待后续独立阶段审核容量上限、保留期与清理。LOG_CONTENT 默认 false；P2 仍必须有数据库级断言：默认未配置和显式 false 时，数据库、日志、事件都没有请求/响应/reasoning 正文以及任何凭据。

不保存完整 kwargs、metadata、头、配置或异常文本。内容功能将来即使被批准，也不得记录 Authorization、任何 Key 或其他凭据。

## 5. 里程碑（P1 已授权，后续逐阶段授权）

| 阶段 | 内容 | 验收重点 |
|---|---|---|
| P0 验证（含 P0.1） | 版本、无数据库转发、钩子、字段、取消、安全与限制 | 可复现脚本、脱敏机器证据；报告失败及缺口；完成后停下 |
| P1 接入 | 通用客户端配置与启动器 | Windows x64/3.13；预置缓存、禁遥测/远程价格；独立端口；两个合成客户端；数据面安全及兼容性契约 |
| P2 事件库 SQLite | 元数据事件、WAL、单写线程、查询与缺口指标 | 主键幂等；失败去重；尽力投递与丢失可见；默认/显式 false 库级隐私断言；未知/来源正确 |
| P3 控制台改读事件库 | 全局请求与跨客户端统计 | 分页/筛选；刷新保留历史；usage 来源分栏；NULL 不显示 0；费用“估算（非账单）” |
| P4 AstrBot 对账 | 实际客户端接入、取消与供应商用量核对 | 单独真实调用授权；仅 reported；记录窗口及其它流量；可比口径下检验原 2% 目标 |

## 6. P1 验收与待定项

P1：独立启动器在加载第三方启动钩子前核对固定哈希锁环境；拒绝 enterprise、异常启动钩子；预置哈希正确的 tokenizer，不能联网修复；禁遥测/远程价格/数据库/花费日志/fallback/重试/客户端凭据覆盖。生成保管本机 master key，上游 Key 只读环境变量。

事件契约 v0 含调用 ID、时间、header/metadata 两个标签、key_alias、模型别名、stream、provider/client outcome、来源化 usage/费用。仅非阻塞有界队列与非产品 JSONL sink；SQLite 留 P2。

升级契约覆盖普通、SSE、4xx/5xx、缺 usage、首块前/中途断开、回调异常、慢消费者、标签冲突、正文关闭、凭据哨兵及压缩。数据面检查无/错误 Key、Host 伪造、回环监听、管理路由和两个客户端区分；P1.3 按 D09 的允许表验收，另测大小写、尾斜杠、重复斜杠、百分号编码、重定向及 WS 握手。真实调用必须先用完全相同探针（含压缩、实际回调继承结构）预演，另行授权。

许可证和管理认证方式仍待定；P1 不提供新的管理 API。开发参数和验证限制记录在 PROGRESS.md；已批准方案 A 不再列为待选架构。事实与历史证据见 P0（原工程 `docs/SPIKE_LITELLM.md`，本快照未附）、P0.1（原工程 `docs/SPIKE_LITELLM_P01.md`，本快照未附）。
