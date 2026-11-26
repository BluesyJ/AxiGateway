# P7 自有模型与对话：设计及实施计划

2026-11-23；用户授权添加模型、手动对话，明确选择 OpenAI 兼容接口。基线 PR #3 `447cdbe`；本会话在当前分支完成实现及限定验证，更新同一个 PR，用户审核合并。

## 设计与复用

新增「我的模型」「模型对话」：填写显示名、API Base URL、上游模型ID和API Key，保存后选择模型发送多轮文字消息。支持HTTPS服务和回环HTTP本地模型；保存、刷新不发送探测，仅点击发送调用。最大输出默认512 token，可调1–4096。对话仅在页面内存，刷新清空。

配置存于 `data/playground/models.dpapi`，Windows当前用户DPAPI加密并限制目录ACL；API只返回非敏感配置及Key配置状态。编辑留空Key表示保留；变更接口地址必须重填Key，防止转发旧凭据。删除移除本机配置。自动测试使用`.cache/`独立合成数据。

对话提交模型配置版本；版本已变更则返回409，不把旧上下文发往新配置，刷新后清空该上下文。消息长度受262144字节总请求体约束，不因合法长回复超过单条阈值而阻断续聊。

UI仍为`127.0.0.1:8790`，保留Host/Origin/CSP。真实模型使用原LiteLLM master模式，浏览器不获取master，不使用demo签名。首次请求按选择启动独立Proxy，切模型时替换该子进程，调用串行；上游Key只经子进程环境传递。元数据存各模型的`data/playground/proxies/<id>/events/`。不碰4000或旧库，demo授权仍只演示假上游。本机操作员管理模型，无企业登录。

2026-11-23已读 [LiteLLM README及部署](https://github.com/BerriAI/litellm/blob/main/README.md)、[MIT范围](https://github.com/BerriAI/litellm/blob/main/LICENSE)和11月20日发布记录；复用锁定1.106.5的`openai/`前缀、环境凭据、原Proxy，不升级、不读enterprise。已读 [Open WebUI README及部署](https://github.com/open-webui/open-webui/blob/main/README.md)、[许可证](https://github.com/open-webui/open-webui/blob/main/LICENSE)和11月17日发布记录：支持兼容服务，但另加Python/Docker服务及数据卷，许可含品牌限制。本项目已有UI/Proxy/事件采集，直接扩展，不复制其源码。LibreChat两文件接口访问失败，不据此下结论。

## 文件与顺序

1. 先写`tests_p7/test_local_models.py`证明缺少功能，再实现`playground/models.py`（校验、加密、公开投影）、`model_runtime.py`（原Proxy生命周期/调用）、`model_api.py`（管理及对话路由）。
2. `launcher.py`只扩展OpenAI兼容模型配置校验，DeepSeek/允许表保留；`playground/server.py`和`scripts/start_playground.py`挂载新接口、关闭自建进程。新增UI`src/models.tsx`，修改`playground.tsx/css`导航和真实/演示标识。
3. 新增`console_ui/model-tests/`、`models.config.ts`及假上游入口，验证添加→选择→连续对话→编辑/删除→重启读回；补CI、SPEC、SETUP、ROADMAP、HANDOFF、PROGRESS和结论JSON。不新增依赖。

## 验证预算

1轮实现、1次独立复核修复、1个工作日；新增后端≤10个逻辑用例、浏览器≤6个场景、功能假调用≤30次；核心、原16契约、既有前端、构建/类型、Ruff各一次，失败或变化仅复测受影响项。助手真实调用0。重点：坏配置不覆盖，改地址不复用旧Key，模型切换不串凭据/对话，错误不伪造回复，正文与Key不进入持久化事件或错误响应。达到上限/范围外问题登记后停止扩展。

## 不做的事

原生Claude/Gemini、企业登录、真实应用授权、Key分发、Agent/MCP、流式/附件/正文留存、自动重试/探测、价格计算、依赖升级、旧服务改造；助手不读取用户Key或调用真实模型。
