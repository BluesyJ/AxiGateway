# Windows 安装与试用

使用 PowerShell，准备 Git、CPython 3.13 x64 和 Node 24。公开仓库可直接克隆：

```powershell
git clone https://github.com/BluesyJ/AxiGateway.git
Set-Location AxiGateway
```

以下命令均在仓库根目录执行。两个 Python 环境独立，依赖保持原锁。旧版 `pip install -e .` 和 `python -m app` 不适用于当前系统。

## 1. 安装依赖

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
py -3.13 -m venv --without-pip .venv-litellm
New-Item -ItemType Directory -Force .cache\wheels | Out-Null
py -3.13 -m pip download --require-hashes --only-binary=:all: -r spikes\litellm_p0\requirements-win-py313.lock.txt -d .cache\wheels
py -3.13 -m pip --python .venv-litellm\Scripts\python.exe install --no-index --find-links .cache\wheels --require-hashes --only-binary=:all: -r spikes\litellm_p0\requirements-win-py313.lock.txt
.\.venv-litellm\Scripts\python.exe -I -S scripts\start_p1.py --check
Push-Location console_ui
npm.cmd ci
npm.cmd run build
Pop-Location
```

下载是操作员显式安装步骤，启动器不会联网修复。若原锁中的 wheel 无法获取，停止并提供匹配原哈希的资产，不能换版本或重建清单绕过。不要向 `.venv-litellm` 安装 pip/pytest/ruff 等额外包。开发锁为原版本快照，代理锁为完整哈希锁；两者保障不同。本轮实际安装依据见 [整理结果](SNAPSHOT.md)。

## 2. 不用 Key 的界面演示

```powershell
.\.venv-litellm\Scripts\python.exe -I -S scripts\make_demo.py
.\.venv\Scripts\python.exe -m p3_console.server
```

访问 <http://127.0.0.1:8788/?source=demo>。生成入口先校验严格运行环境，再启用 Windows ACL 依赖；开发环境不需要安装 pywin32。脚本只调用进程内假上游，生成 `data/demo/demo-events.sqlite3`；已有演示库会拒绝覆盖。演示 token/费用为合成数据，不是账单。Ctrl+C 结束。若 8788 已被原工程占用，可给查询服务加 `--port 18788` 并访问对应端口；不要停止原工程服务。

## 3. 自动验证网关链路

```powershell
.\.venv\Scripts\python.exe -B scripts\test_core.py
.\.venv\Scripts\python.exe -B scripts\test_core.py --contracts
```

第二条会在临时回环端口启动真实网关和假上游，使用合成凭据与独立库，阻断非回环连接。覆盖普通/SSE/压缩/失败/中断等；不是调用真实 DeepSeek。详情见 [TESTING](TESTING.md)。

## 4. 日后经授权使用真实上游

本轮未运行。由操作员运行 `& .\scripts\start-prod.ps1`，在隐藏输入提示中输入上游 Key，仅传子进程。代理就绪后用 `& .\scripts\start-prod.ps1 -CopyKey` 复制新机本地网关 Key，客户端地址填 `http://127.0.0.1:4000/v1`，模型名保持模板一致。控制台用 `?source=live` 查看实际库。按需 Ctrl+C 停止并关闭终端。

业务路径仅 `POST /v1/chat/completions`、`GET /v1/models`；不要依赖 401 自动重试。AstrBot 主模型/备用及插件需分别确认，不能依据模板推定已切换。新电脑的 Key 与 DPAPI 独立生成。原 12 次真实冒烟额度不迁移、不重置，本快照未附带冒烟入口。

## 5. 模型访问管理后端演示

完成原锁环境安装后运行：

```powershell
.\.venv-litellm\Scripts\python.exe -I -S scripts\demo_access.py
```

脚本在独立 `.cache/access-demo/<run>/demo/` 建立 `demo-policy.sqlite3` 和 `proxy/events/events.sqlite3`，启动空闲回环端口上的管理 API、LiteLLM 及假上游。它创建 2 个合成应用，执行 2 个模型别名上的 100 次允许调用，再验证越权、RPM、停用与旧 token 拒绝。签名私钥和 token 只保留在进程内，结束自动关闭本次服务；输出只含受控结论。该入口用于后端演示和验收，不是带登录页面的产品版本。

独立管理进程入口为 `scripts/start_access.py --config <demo-trust.json> --port 8789`，仍须使用上述 `-I -S` 运行环境。配置含 `demo=true`、模型别名、管理员标识、issuer、两个独立 audience、RSA 公钥 PEM 和 demo 数据库相对路径；格式见演示生成代码。`--check` 只校验启动条件。它不签发凭据；管理调用需签发方提供有效的短期 RS256 token，自动演示使用的凭据结束即丢弃。不要把 master key 填入管理接口，也不要把该模式接入真实上游。

| 接口 | 行为 |
|---|---|
| `GET /api/apps` | 返回应用策略与配置模型目录，最多 1000 个应用 |
| `PUT /api/apps/{id}` | 完整替换 `models/rpm/enabled/expected_revision`；新建版本为 0，冲突返回 409 |
| `GET /api/audit?limit=100` | 返回最近 1–100 条治理变更，不包含调用正文或凭据 |

管理面只绑定 `127.0.0.1:8789`，无跨域；异常 Host/Origin 被拒。应用与管理 audience 分离，验证签名、issuer、有效期和管理员名单。RPM 按 UTC 自然分钟计准入请求，不是 Token/费用额度；停用阻断后续准入，不取消已发往上游的请求。8788 继续只读、无登录。演示操作页面见下一节，正式登录流程后续开发。

演示模式只接受标准聊天字段，字段清单见 `axi_gateway/access/protocol.py`；请求级备用路由、重试和上游配置扩展一律拒绝。`demo_access.py --rejections-only` 只执行 4 个拒绝探测，上游调用应为 0，适用于该边界的针对性复测。管理启动器拒绝 4000、8787、8788 等已保留端口。

## 6. 内置 Playground 手动试用

完成第 1 节的 Python 锁环境恢复，并使用 Node 24 构建前端：

```powershell
Set-Location console_ui
npm.cmd ci --ignore-scripts --no-audit --no-fund
npm.cmd run build
Set-Location ..
.\.venv-litellm\Scripts\python.exe -I -S scripts\start_playground.py
```

打开 <http://127.0.0.1:8790/>。若端口已占用，启动命令加 `--port 18790` 并打开对应地址；4000、8787、8788、8789 为保留端口。Ctrl+C 结束本次服务。所有服务仅监听 `127.0.0.1`，LiteLLM 和假上游使用临时端口。

1. 「调用试验」选择阿汐模拟应用和模型 A，发送请求，查看固定假回复。改选模型 B 会因未授权而被拒。
2. 「应用权限」给阿汐勾选模型 B 并保存，返回调用页再试。为知识库应用设置每分钟上限 1，在同一自然分钟内连续调用两次可看到 429；停用应用后新请求被拒。
3. 「调用记录」查看 P2 元数据；「治理审计」查看授权、RPM 和停用的变更。刷新保留本次运行的数据；重启创建全新演示环境。

每次运行独立保存到 `.cache/playground/<run>/demo/`：`demo-policy.sqlite3` 为策略和审计，`proxy/events/events.sqlite3` 为调用元数据。历史 demo 不覆盖，界面仅展示当前运行。输入正文不留存，JWT 不返回浏览器；私钥、token、上游凭据和离线演示 master key 均只在内存。调用回复与用量为合成数据，无真实模型调用。浏览器连接中断时，页面显示结果未知，可恢复连接后查看记录；事件仍是尽力投递。

本机可访问该服务的操作员可以管理全部合成应用；本阶段没有正式管理登录。**不做的事**：真实上游配置、企业 IdP、Agent/MCP 执行、Key 发放、正文留存和 Token/费用额度。
