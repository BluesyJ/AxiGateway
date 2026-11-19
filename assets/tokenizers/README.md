# 预置 tokenizer 静态资产

只包含 tokenizer 数据；没有 Python 启动代码。启动器逐文件核对 SHA-256 后复制到忽略目录，缺失/损坏即拒绝启动，不联网下载或修复。来源为 P0 已下载、P0.1 已核对的官方资产；本阶段没有重新下载。

| 编码 | 官方来源 | 缓存文件名 | SHA-256 |
|---|---|---|---|
| cl100k_base | [OpenAI 静态资产](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken) | `9b5ad71b2ce5302211f9c61530b329a4922fc6a4` | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |
| o200k_base | [OpenAI 静态资产](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken) | `fb374d419588a4632f3f557e76b4b70aebbca790` | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |

对应加载及期望哈希可在锁定 tiktoken 的 `tiktoken_ext/openai_public.py` 核对。LiteLLM Windows wheel 内置 cl100k 文件的 CRLF 缺陷见 P0.1 报告；不修改其安装包。项目许可证尚待选择，正式发布时仍须审查静态资产的分发与第三方许可说明。
