# P6 控制台登录 / P7 模型与 Key 管理：首个开发增量

2026-11-20。用户要求本机直接规划并开发，完成后通过 PR 审核合并。本增量交付可运行的模型访问管理后端与假上游演示，不宣称完成产品第二版本。

## 交付与选择

交付应用授权、模型范围、每分钟请求限制、应用停用、治理审计和自动演示。2 个合成应用使用 2 个模型别名，100 次允许调用经过真实 LiteLLM 及假上游；越权、限流及停用请求在上游之前拒绝。治理变更与审计在独立 SQLite 事务内提交，失败不返回成功；事件库继续尽力采集，不承担授权账本。

复用 LiteLLM 1.106.5 的 OSS `custom_auth` 和锁内 PyJWT 2.15.1，以固定 RS256、公钥、issuer、audience、有效期验证外部签发身份。网关不签发产品凭据、不保存私钥或用户密码；测试脚本只在内存生成短期演示签名和身份。管理身份使用独立 audience 与明确管理员名单，应用身份不能调用管理接口。公钥配置变更需重启。首版不含登录页面、OIDC 重定向或企业身份同步。

复核修复：`access/protocol.py` 将演示请求限定为标准 Chat Completions 字段，拒绝请求级 fallback、重试、地址与凭据覆盖，避免只校验主模型后由备用路由绕过许可。启动参数互斥在密钥操作前检查；管理进程禁止使用 4000、8787、8788。

2026-11-20 复核了 [LiteLLM](https://github.com/BerriAI/litellm) 的本地受信 wheel、README 安装说明、MIT 许可证及 OSS 扩展调用链；不读取 enterprise。复核 [PyJWT](https://github.com/jpadilla/pyjwt) README、安装与验证示例及 MIT 许可证，直接复用原锁版本。复核 [Keycloak](https://github.com/keycloak/keycloak) README、独立服务部署方式与 Apache-2.0 许可证：适合后续企业登录和用户管理，但本增量不引入 Java 服务、身份数据库或新依赖。公开发布查询受到 GitHub API 限流，本轮不引用热度和最近发布结论。

## 实现计划与文件清单

1. `axi_gateway/access/{identity,policy}.py`：验证身份；独立策略库保存应用、模型许可、RPM 窗口与治理审计。`tests_p7/test_access.py` 先写拒绝、事务回滚、版本冲突、并发限流测试，再实现。RPM 统计已准入的模型请求，包含随后失败的上游请求；GET 模型列表不消耗 RPM，按 UTC 自然分钟计数，边界可短时突发，不是 Token/费用额度。
2. `axi_gateway/access/{hook,server}.py`、`axi_gateway/launcher.py`：LiteLLM 扩展执行认证和授权；独立管理 API 绑定 127.0.0.1:8789，精确 Host/Origin、无跨域、受控错误、禁止诊断输出。`events.py/callbacks.py` 仅在该模式投影框架清理后的可信应用标识。原 master 模式及 ExposureGate 不变。
3. `scripts/demo_access.py`、`scripts/start_access.py`、`tests_p7/run_access.py`：独立启动入口、内存合成身份、独立 demo 数据目录、假上游、100 次允许调用及拒绝场景；受控结论写入忽略目录。修改 `pyproject.toml` 纳入新测试，CI 添加同一演示入口，补充 SETUP/TESTING/ENGINEERING_HANDOFF/SPEC/ROADMAP/README/CONTRIBUTING/START_HERE 与 PROGRESS。前端与依赖锁不变。

端口：原 8787 不动；模型 4000、只读统计 8788、管理 8789。自动演示使用系统分配的空闲回环端口，不占用已有服务。新身份模式只在 `--offline` 与独立 demo 目录启用。

## 验证预算与验收

最多 1 轮实现、1 次有界修复、1 个工作日；最多 20 个新增逻辑用例，100 次允许假调用、最多 20 次拒绝探测、原 16 契约场景各 1 次；真实调用 0 次。先观察新测试因缺少功能失败，再跑新增测试、核心测试、16 契约、ruff、仓库检查各一次。出现实现失败只执行对应修复和受影响回归；发现范围外问题记录待决并停止扩查。未改前端，不重复构建和截图。

通过后提交开发 PR，负责人验收合并；未通过则保留失败结论和可复现入口，不标完成。复核关注：伪造身份元数据、管理员与应用凭据混用、并发准入、停用后旧 token、存储失败及状态与审计原子性。

## 不做的事

不改 legacy、8787、AstrBot、数据面允许路由、锁文件或原历史证据；不做真实调用、虚拟 Key、企业登录部署、管理页面、MCP/Skill/Agent、Token/费用额度、可靠调用投递、价格计算或内容记录；不选择项目许可证，不合并 PR。旧 P4.2 未收尾入口、P4.3 对账与 P4.4 本地模型继续延期，保留原状态。
