# 事件契约 v0 / SQLite schema 2

> 协作快照说明（2026-11-19）：批准规则保持原意；文中 legacy、旧报告、原阶段授权等历史描述指原工程，原件不在此快照。当前阶段及状态以 docs/ROADMAP.md 为准。整理不授予真实调用或上传权限。

P4.0b 按 2026-11-05 批准记录更新 D03/D04。字段布局、隐私边界和历史行保留。


| 字段 | 约束与含义 |
|---|---|
| schema_version | 固定 0 |
| litellm_call_id | LiteLLM 调用 UUID；非法/缺失则放弃事件并增加 projection_failed，不伪造 ID |
| started_at / ended_at / duration_ms | LiteLLM 1.106.5 的无时区回调时间按网关主机在事件发生时的本地时区转为 UTC；有时区的值按自身时区转为 UTC。起止存 ISO 8601，毫秒耗时按两个 UTC 时刻之差计算，缺失为 NULL。 |
| client_label.header / .metadata | 分别来自 x-axi-client 和 metadata.client_label，冲突保留两值；都不可信 |
| key_alias | 当前单 master key 模式为 NULL；不保存 Key 或 Key 哈希 |
| model_alias / stream | 本机 profile 配置的别名 / LiteLLM 回调中的流式标志 |
| provider_outcome | success/error 对应 LiteLLM success/failure logging 观察，**不是供应商完整生成/扣费证明**；被框架标为转发前拒绝的回调已排除 |
| client_outcome | 仅流式且有可信框架断开标记为 client_disconnected；其他情况 unknown；不使用 client_aborted |
| usage.prompt_tokens / completion_tokens / total_tokens | 只保留有限非负整数；允许 NULL，不把 reasoning 再加到 total |
| usage.usage_source | LiteLLM 1.106.5 的非流式 success、无客户端断开且 prompt / total 均为合法正整数时为 reported。普通流式有 usage 为 unknown，可信断开为 estimated。无 usage 对象为 missing，非流式框架补出的 0/0/0 为 unknown。 |
| usage.cache_hit_tokens / cache_miss_tokens / reasoning_tokens | 仅 reported 允许细分。优先合法的原始命中与未命中，原始 miss 标 reported；缺原始 miss 时按合法 prompt − hit 推导并标 derived。reasoning 为 completion 子集。 |
| cost.amount / currency / source / price_version | 内置表覆盖该模型且回调提供可用费用时，记录金额、USD、litellm_builtin、版本和完整价目文件 SHA-256；缺失/未覆盖/失败费用为 NULL；未经证实的 0 也为 NULL |
| cost.estimate_not_invoice | 固定 true；展示必须标“估算（非账单）”；user_prices 预留，不自行填写价格 |
| error_category | 固定分类 upstream_http / provider_or_transport，禁止原始异常文本；前者只说明异常带 HTTP 错误码，不证明供应商收到请求 |

对账只纳入 reported。库中既有 unknown 行保留原分类。版本升级时须重新验证非流式缺 usage、正常响应、流式与断开四种行为。

### D02 依据与判定

依据为用户 P1.1 明确批准和 P11_CONTRACT_RESULT.json（原工程 `P11_CONTRACT_RESULT.json`，本快照未附）。仅 `kwargs.stream is True` 时，读取 `standard_logging_object.error_information.error_code`（499/"499"）、`error_class`（ClientDisconnected）或 `litellm_params.metadata.client_disconnected is True`。任一框架标记成立即 outcome=client_disconnected、source=estimated；没有数值仍保留 NULL，不编造用量。estimated 的缓存/reasoning 都为 NULL。

不读取原始 `proxy_server_request.body` 中的标记，不从任意异常文本或顶层请求 metadata 猜测。锁定 LiteLLM 会清理伪造标准日志/断开标记；契约验证正常 SSE 的伪造字段仍得到 unknown。非流式即使出现同名标记也保持 unknown。此分类说明框架观察到连接断开，不证明用户主动取消、上游停止生成或真实账单用量。

### 转发前拒绝

仅 failure logging 回调且顶层 `litellm_no_upstream_llm_call is True` 时，collector 在事件投影前增加 `auth_rejected` 并返回，不进队列或任何 sink，不生成 provider_or_transport 事件。该服务端标记来自锁内 LiteLLM `proxy/utils.py:_handle_logging_proxy_only_error`，常量定义在 constants.py；不基于 401/400/500 或错误文本猜测。

该计数表示框架观察到的代理门禁拒绝，包含鉴权/凭据覆盖等，不是全 HTTP 访问计数。LiteLLM 对部分管理/参数校验拒绝不发 SDK failure 回调，因此这些请求也不产生事件，但不在该计数中；全路由报告单独记录每次 HTTP 状态。伪造同名顶层字段后诱发真实假上游 429 的契约仍写失败事件，验证不能借此隐藏供应商失败。

标签只接受 1–64 个 ASCII 字母/数字及 `_.:-`，超长、无效、凭据形态或包含已知 Key 的值为 NULL。不得把正文或凭据放在标签里；任意自报字符串无法从语义上证明它不是内容，P1 只保证白名单、不复制正文/原始元数据及过滤已知凭据。实际使用的上游/master Key 从内存用于拒绝匹配，不输出。中文展示名/更复杂标签协议留审核决定。

## SQLite、查询与投递

默认路径 `data/p1/events/events.sqlite3`（沿用原启动数据目录，不迁移 master key）。事件子目录的受保护、可继承 Windows DACL 仅允许当前用户；SQLite/WAL/SHM 与可选 JSONL 继承此权限，重启时收紧现有同名文件。data/ 已忽略。备份整个目录前应停止写入；不要只复制活跃的主文件而遗漏 WAL。

SQLite 写入端以事务从版本 1 迁移到 2，重建 events 的缓存约束与索引。保留原记录及顺序、gaps 和 counters；不对历史用量作事后归类。只读端可打开两个版本。WAL、synchronous=FULL 和单写线程保持原口径。

| 表 | 口径 |
|---|---|
| events | litellm_call_id 主键，首次成功提交的观察获保留，重复 ID 不新增、不覆盖；字段按上表扁平化，标签分别 client_header/client_metadata，费用为 cost_amount/cost_currency/cost_source/price_version。D04/枚举/费用完整性有 SQL CHECK；NULL 不补 0。 |
| gaps | 每进程 run_id＋受控原因一行，count 是该原因累计丢失数，first_at/last_at 是首末观察时间窗口，窗口内不代表持续故障；仅数量/时间/原因，不存丢失事件内容。 |
| counters | 跨进程累计 accepted/written/auth_rejected、queue_full/projection_failed/sink_failed/rejected_after_close、storage_failed/debug_failed；事务增量更新。旧 collector-status.json 不再创建/更新，也不自动删除或导入。 |

实际继承仍是 GatewayCallback → CustomLogger。回调只白名单投影和 put_nowait，短内存锁不持有 I/O；队列默认 1024，profile 可设 1–65536。消费线程每批最多 64 个事件，空闲约 100ms 同步计数/缺口；事件、计数增量、累计 gap 在同一事务提交。写失败的整批事件丢弃，增加 sink_failed；每次失败事务另增 storage_failed（不是额外丢失数）。数据库恢复后的事务补写内存中的计数与 gap。重复 ID 的 written 表示提交的观察次数，并非 events 行数；查询行数以 events 为准。

拒绝标记在投影前只增加 auth_rejected，不入 events、不计 loss_count。进程内 loss_count 为四种丢失原因之和，持久值可由 counters 同样求和；不扩展全量 HTTP 日志。队列大小是运行时状态，不伪装成持久准确快照。

退出等待默认 2 秒，`--drain-timeout` 可设 0–30 秒；尽力排空，超时不启动更多事务，可能仍有正在执行的 I/O。超时/强杀会丢失内存队列及未提交计数、gap；磁盘恢复之前进程退出的缺口不能追补，**不承诺每次丢失都必然可见**。已提交事务与未提交事务分开验收；不承诺掉电等同于本轮强杀测试。

`--debug-jsonl` 在数据库提交成功后镜像元数据，非产品、不承诺持久去重/保留；调试写失败只增 debug_failed，不撤销已提交事件、不计数据库丢失。只提供开发辅助，不是第二份产品事件库。

`queries.py` 提供 read_connection、list_events、aggregate_events、list_gaps、read_counters。连接 mode=ro + query_only；每次查询短连接，WAL 下读事务不阻塞提交。列表按插入顺序分页，limit≤1000；支持 UTC 时间窗口 [start,end)、模型、任一客户端标签、usage_source 筛选。聚合可按 UTC 小时/模型/两个标签/来源，始终进一步按 usage_source 和费用币种/来源/价目版本分组，不混合实报与估算。SQL SUM 全未知返回 NULL；部分已知返回已知小计，须结合 known_count/event_count 展示，不冒充完整总量。reasoning 不重复加到 total；费用仍标“估算（非账单）”，不新增价格计算。没有 HTTP 查询入口。

## 隐私边界

默认和显式 LOG_CONTENT=false 都只写声明字段；LOG_CONTENT=true 拒绝启动。禁止 Authorization、Cookie、Key、完整请求头/kwargs/metadata、正文、reasoning 正文和原始异常。第三方控制台日志被丢弃，启动失败只输出受控代码；详细诊断牺牲可见性以避免泄漏。测试探针只额外保存布尔标记、固定异常类型和源码位置，不保存异常原文。

P2 验证包括默认未配置/显式 false 的成功、失败、流式、中断，物理数据库、WAL/SHM、事件和日志哨兵扫描；实跑结论见 P2_RESULT.json，未通过项目不得声称验收完成。
