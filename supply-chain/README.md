# 固定运行环境

原 LiteLLM 1.106.5、117 项依赖与 runtime-manifest.json 原字节保留。
锁路径 `spikes/litellm_p0/requirements-win-py313.lock.txt` 保持不变，因为启动器校验这个路径。
锁 SHA-256：`468edc1de68db234a938c8b12f4a3a091d5e2529e3ef32784741b030dc8ba113`。
安装见 [SETUP](../docs/SETUP.md)。原公告核对证据为原工程 `docs/SPIKE_LITELLM_P01_SECURITY.json`，本次未重新选版、未改锁。
启动必须使用 `-I -S`，不添加第三方启动钩子、不安装 enterprise、不联网修复；只有通过静态校验才运行获准的 pywin32.pth。
