# P7 真实调用监控：设计与实施计划

2026-11-23。用户批准补齐真实监控，并要求模型统计、调用人、正文、延时及结果。按writing-plans/executing-plans在当前功能分支实施，完成后一次独立复核，用户决定合并。

## 设计与复用

复用P2事件库及只读连接；服务生成UUID，经锁定LiteLLM支持的`x-litellm-call-id`关联请求。各模型原库保留；新增`data/playground/monitor.sqlite3`记录本机请求身份、配置快照、端到端耗时、受控错误及可选DPAPI正文。模型配置与操作审计在原DPAPI文档中原子保存，兼容旧格式；删除模型保留历史。查询失败明确报错，不伪装空列表。

主导航提供真实调用记录和治理审计，旧记录移到明确的演示入口。请求按时间、模型、结果、来源筛选并分页；按模型计数/结果/平均耗时，按来源统计输入/输出/总token。详情显示两种结果、关联ID、调用人、配置版本、缓存/推理token及可选对话。调用人只有本机操作员（未登录）或未知，不虚构员工。

用户最新正文查看要求扩展旧D12：每次发送前可选“保存本次对话内容”，默认关闭；仅在独立库以Windows当前用户DPAPI密文保存请求消息与文字回复，当前上游Key/master精确遮蔽；原P2及进程日志继续无正文。原始上游错误不保存。未开启、历史未保存、保存失败分别显示。详情查询不调用模型。

2026-11-23已核对[LiteLLM README/部署](https://github.com/BerriAI/litellm/blob/main/README.md)及[MIT范围](https://github.com/BerriAI/litellm/blob/main/LICENSE)，复用1.106.5及现有采集；已核对[Langfuse README](https://github.com/langfuse/langfuse/blob/main/README.md)、[compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)、[许可证](https://github.com/langfuse/langfuse/blob/main/LICENSE)：观测匹配，但需要Postgres/ClickHouse/Redis/对象存储，默认有可关闭遥测；非企业目录MIT。此处补本机闭环，不引入新平台或复制源码，不读enterprise。

## 文件与执行顺序

1. `tests_p7/test_monitor.py`先证明缺少查询、正文开关及审计，再实现`monitor.py/monitor_queries.py/models.py`。覆盖跨库SQL一致/NULL/筛选分页、历史只读、删除后记录、默认无正文/开启后加密脱敏、受控失败、同源及审计回滚。
2. `model_runtime.py/model_api.py`关联开始/结束及配置快照；P2来源与token保持权威，不猜测、不重复计数。对话返回关联ID；记录失败不诱导重试已完成调用。查询接口白名单并经同源门禁。
3. `console_ui/src/monitor.tsx`及导航/样式/对话接通真实记录；详情点击加载正文；UTC存储、本地显示。6个浏览器场景覆盖正常/空/错误/加载/窄屏，全部合成数据。
4. 核心、原16契约、三组前端、构建/类型、Ruff、仓库检查；一次复核只修本轮问题。更新SETUP/SPEC/HANDOFF与结论JSON，旧证据保留。

复核重点：旧格式与已删除模型、失败和未完成请求、未知用量、正文关闭与Key哨兵、跨配置版本归属。预算及不做事项见[PROGRESS](../PROGRESS.md)。沿用原依赖、回环8790；不改旧服务。
