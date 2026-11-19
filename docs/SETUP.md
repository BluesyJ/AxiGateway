# Windows 安装与试用

使用 PowerShell，准备 Git、CPython 3.13 x64 和 Node 24。先登录有仓库访问权限的 GitHub 账号，再克隆：

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
