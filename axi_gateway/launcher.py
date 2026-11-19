"""Launch the unmodified OSS Proxy with a locked, local, database-free profile."""

import argparse
import contextlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import site
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from axi_gateway.bootstrap import (
    StartupError,
    prepare_tokenizers,
    sha256,
    verify_environment,
)

ROOT = Path(__file__).resolve().parents[1]


def loopback_guard(event: str, args: tuple[object, ...]) -> None:
    """Used for preloading and offline contracts: reject non-loopback sockets."""
    if event == "socket.getaddrinfo":
        host = args[0]
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host not in {None, "localhost", "127.0.0.1", "::1"}:
            raise OSError("offline network denied")
    if event == "socket.connect" and isinstance(args[1], tuple):
        try:
            allowed = ipaddress.ip_address(args[1][0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise OSError("offline network denied")


def read_settings(path: Path) -> dict[str, object]:
    """Load a small allowlisted profile; never accept arbitrary LiteLLM YAML."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf8"))
    single = {"model_alias", "provider_model", "api_base", "queue_capacity"}
    multiple = {"models", "api_base", "queue_capacity"}
    if not isinstance(data, dict) or set(data) not in (single, multiple):
        raise StartupError("invalid_config_fields")
    models = configured_models(data)
    if not isinstance(models, list) or not 1 <= len(models) <= 32:
        raise StartupError("invalid_models")
    aliases = set()
    for model in models:
        if not isinstance(model, dict) or set(model) != {
            "model_alias",
            "provider_model",
        }:
            raise StartupError("invalid_model_fields")
        alias = model["model_alias"]
        if not isinstance(alias, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", alias
        ):
            raise StartupError("invalid_model_alias")
        if alias in aliases:
            raise StartupError("duplicate_model_alias")
        aliases.add(alias)
        if not isinstance(model["provider_model"], str) or not re.fullmatch(
            r"deepseek/[A-Za-z0-9_.-]{1,64}", model["provider_model"]
        ):
            raise StartupError("unsupported_provider_profile")
    if not isinstance(data["api_base"], str):
        raise StartupError("invalid_api_base")
    url = urlsplit(data["api_base"])
    local = url.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or (not local and url.scheme != "https")
    ):
        raise StartupError("invalid_api_base")
    if (
        type(data["queue_capacity"]) is not int
        or not 1 <= data["queue_capacity"] <= 65536
    ):
        raise StartupError("invalid_queue_capacity")
    return data


def configured_models(settings: dict) -> list[dict]:
    """Accept both the legacy single-model profile and the explicit model list."""
    if "models" in settings:
        return settings["models"]
    return [{key: settings[key] for key in ("model_alias", "provider_model")}]


def proxy_configuration(settings: dict[str, object]) -> dict[str, object]:
    """Generate fixed Proxy settings with environment references for credentials."""
    return {
        "model_list": [
            {
                "model_name": model["model_alias"],
                "litellm_params": {
                    "model": model["provider_model"],
                    "api_base": settings["api_base"],
                    "api_key": "os.environ/UPSTREAM_API_KEY",
                    "timeout": 60,
                },
            }
            for model in configured_models(settings)
        ],
        "litellm_settings": {
            "callbacks": ["axi_gateway.callbacks.callback"],
            "set_verbose": False,
            "turn_off_message_logging": True,
            "log_raw_request_response": False,
            "telemetry": False,
            "num_retries": 0,
            "request_timeout": 60,
            "fallbacks": [],
            "default_fallbacks": [],
            "cache": False,
        },
        "router_settings": {
            "num_retries": 0,
            "disable_cooldowns": True,
            "fallbacks": [],
            "context_window_fallbacks": [],
            "content_policy_fallbacks": [],
        },
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "disable_spend_logs": True,
            "disable_error_logs": True,
            "allow_client_side_credentials": False,
            "store_model_in_db": False,
        },
    }


class Quiet(io.TextIOBase):
    """Discard third-party diagnostics that may contain arbitrary provider text."""

    def write(self, value: str) -> int:
        """Never persist or forward the supplied text."""
        return len(value)

    def flush(self) -> None:
        """No output buffer exists."""


def main(
    argv: list[str] | None = None, before_server: Callable[[], None] | None = None
) -> int:
    """Validate before importing dependencies, then run one loopback Proxy worker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv-litellm")
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config/litellm.example.yaml"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/p1")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--drain-timeout", type=float, default=2.0)
    parser.add_argument(
        "--debug-jsonl", action="store_true", help="NON-PRODUCT metadata debug output"
    )
    parser.add_argument(
        "--offline", action="store_true", help="Reject all non-loopback sockets"
    )
    parser.add_argument(
        "--check", action="store_true", help="Verify environment/assets only; no server"
    )
    parser.add_argument(
        "--copy-key", action="store_true", help="Copy the local master key to clipboard"
    )
    args = parser.parse_args(argv)
    try:
        if not sys.flags.no_site or not sys.flags.isolated:
            raise StartupError("use_python_isolated_no_site")
        if not 1 <= args.port <= 65535 or args.port == 8787:
            raise StartupError("invalid_or_legacy_port")
        if not math.isfinite(args.drain_timeout) or not 0 <= args.drain_timeout <= 30:
            raise StartupError("invalid_drain_timeout")
        venv = args.venv.resolve()
        data_dir = args.data_dir.resolve()
        # Runtime state must remain inside a gitignored workspace data/cache directory.
        if not any(
            data_dir.is_relative_to(ROOT / directory)
            for directory in ("data", ".cache")
        ):
            raise StartupError("unsafe_data_directory")
        checked = verify_environment(ROOT, venv)
        prepare_tokenizers(ROOT, data_dir / "tokenizers")
        if args.check:
            print(json.dumps({"event": "preflight_ok", **checked}))
            return 0
        if os.environ.get("LOG_CONTENT", "false").lower() not in {"false", "0"}:
            raise StartupError("content_logging_unsupported")
        upstream = os.environ.get("UPSTREAM_API_KEY", "")
        if not upstream and not args.copy_key:
            raise StartupError("upstream_key_environment_required")
        permitted = {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "COMSPEC",
            "PATHEXT",
            "SYSTEMDRIVE",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        }
        environment = {k: v for k, v in os.environ.items() if k.upper() in permitted}
        environment.update(
            {
                "UPSTREAM_API_KEY": upstream,
                "PYTHON_DOTENV_DISABLED": "1",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "LITELLM_TELEMETRY": "False",
                "DO_NOT_TRACK": "1",
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "LITELLM_LOG": "CRITICAL",
                "TIKTOKEN_CACHE_DIR": str(data_dir / "tokenizers"),
                "DISABLE_ADMIN_UI": "True",
                "NO_DOCS": "True",
                "NO_OPENAPI": "True",
                "NO_REDOC": "True",
                "LOG_CONTENT": "false",
            }
        )
        os.environ.clear()
        os.environ.update(environment)
        preload_only = [True]

        def audit(event: str, values: tuple[object, ...]) -> None:
            if args.offline or preload_only[0]:
                loopback_guard(event, values)

        sys.addaudithook(audit)
        logging.disable(logging.CRITICAL)
        # -S avoids executable .pth before validation; only now enable the checked site.
        sys.prefix = sys.exec_prefix = str(venv)
        site.addsitedir(str(venv / "Lib/site-packages"))
        from axi_gateway.secrets import copy_key, master_key, private_event_directory

        master = master_key(data_dir / "master.key.dpapi")
        if args.copy_key:
            copy_key(master)
            print(json.dumps({"event": "master_key_copied_to_clipboard"}))
            return 0
        os.environ["LITELLM_MASTER_KEY"] = master
        event_dir = data_dir / "events"
        private_event_directory(event_dir)
        settings = read_settings(args.config)
        if master in str(settings) or upstream in str(settings):
            raise StartupError("credential_in_config")
        if args.offline and urlsplit(str(settings["api_base"])).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise StartupError("offline_requires_loopback_upstream")
        import tiktoken

        tiktoken.get_encoding("cl100k_base")
        tiktoken.get_encoding("o200k_base")
        cost_path = (
            venv
            / "Lib/site-packages/litellm/model_prices_and_context_window_backup.json"
        )
        prices = json.loads(cost_path.read_bytes())
        models = configured_models(settings)
        priced = set()
        for model in models:
            provider_model = model["provider_model"]
            priced.update(
                {provider_model, provider_model.split("/", 1)[1]} & prices.keys()
            )
        os.environ["AXI_COLLECTOR_SETTINGS"] = json.dumps(
            {
                "alias": settings.get("model_alias"),
                "litellm_version": checked["litellm_version"],
                "aliases": [model["model_alias"] for model in models],
                "priced_models": sorted(priced),
                "price_version": "litellm-1.106.5:sha256:" + sha256(cost_path),
                "queue_capacity": settings["queue_capacity"],
                "database_path": str(event_dir / "events.sqlite3"),
                "drain_timeout": args.drain_timeout,
                "debug_path": str(event_dir / "development-events.jsonl")
                if args.debug_jsonl
                else None,
            }
        )
        import yaml

        runtime = data_dir / "proxy.runtime.yaml"
        runtime.write_text(
            yaml.safe_dump(proxy_configuration(settings)), encoding="utf8"
        )
        print(
            json.dumps(
                {
                    "event": "starting",
                    "host": "127.0.0.1",
                    "port": args.port,
                    "sink": "sqlite",
                }
            ),
            flush=True,
        )
        with contextlib.redirect_stdout(Quiet()), contextlib.redirect_stderr(Quiet()):
            from litellm.proxy.proxy_cli import run_server
            from litellm.proxy.proxy_server import app

            from axi_gateway.exposure import ExposureGate

            if before_server is not None:
                before_server()
            # Last added is outermost: deny before vendor middleware/auth/routes.
            app.add_middleware(ExposureGate)
            preload_only[0] = False
            run_server.main(
                args=[
                    "--config",
                    str(runtime),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                    "--num_workers",
                    "1",
                ],
                standalone_mode=False,
            )
        return 0
    except StartupError as exc:
        print(json.dumps({"event": "startup_failed", "code": str(exc)}))
        return 1
    except (Exception, SystemExit):
        print(json.dumps({"event": "startup_failed", "code": "runtime_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
