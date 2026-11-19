"""Every LiteLLM upgrade must pass these TCP contracts; no real model traffic."""

import asyncio
import gzip
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Request
from starlette.responses import Response, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.queries import list_events, read_counters  # noqa: E402
from tests.deepseek_fixture import ANSWER, PROMPT, SECRET, DeepSeekFixture  # noqa: E402
from tests.servers import serve  # noqa: E402

RUN = ROOT / "data/p2-contracts" / str(time.time_ns())
PROXY_PYTHON = ROOT / ".venv-litellm/Scripts/python.exe"


class Fixture(DeepSeekFixture):
    """Reuse the fake lifecycle with HTTP compression and error sentinels."""

    compressed = False

    async def chat(self, request: Request) -> Response:
        """Serve normal/failure/stream payloads without connecting to any provider."""
        response = await super().chat(request)
        if self.mode.startswith("error"):
            response = Response(
                json.dumps(
                    {"error": {"message": SECRET + PROMPT, "type": "server_error"}}
                ),
                status_code=int(self.mode[-3:]),
                media_type="application/json",
            )
        if not self.compressed:
            return response
        if isinstance(response, StreamingResponse):

            async def chunks() -> AsyncIterator[bytes]:
                encoder = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
                async for chunk in response.body_iterator:
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    yield encoder.compress(chunk) + encoder.flush(zlib.Z_SYNC_FLUSH)
                yield encoder.flush(zlib.Z_FINISH)

            return StreamingResponse(
                chunks(),
                media_type="text/event-stream",
                headers={"Content-Encoding": "gzip"},
            )
        return Response(
            gzip.compress(response.body),
            status_code=response.status_code,
            media_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )


def load_lines(path: Path) -> list[dict]:
    """Read only complete development/test metadata lines."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def load_key(directory: Path) -> str:
    """Decrypt the master into a private pipe, never the console or a file."""
    # Only the test runner reads this child's pipe into memory for loopback requests.
    code = (
        "import pathlib,win32crypt,sys; "
        "b=pathlib.Path(sys.argv[1]).read_bytes(); "
        "sys.stdout.buffer.write(win32crypt.CryptUnprotectData(b,None,None,None,1)[1])"
    )
    result = subprocess.run(
        [str(PROXY_PYTHON), "-I", "-c", code, str(directory / "master.key.dpapi")],
        capture_output=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("test key decryption failed")
    return result.stdout.decode("ascii")


def load_events(directory: Path) -> list[dict]:
    """Adapt stored columns to the unchanged v0 contract assertions."""
    path = directory / "events/events.sqlite3"
    if not path.exists():
        return []
    result = []
    for row in list_events(path, limit=1000):
        row["client_label"] = {
            "header": row.pop("client_header"),
            "metadata": row.pop("client_metadata"),
        }
        row["usage"] = {
            key: row.pop(key)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "usage_source",
                "cache_hit_tokens",
                "cache_miss_tokens",
                "cache_miss_source",
                "reasoning_tokens",
            )
        }
        row["cost"] = {
            key: row.pop("cost_" + key) for key in ("amount", "currency", "source")
        }
        row["cost"].update(
            price_version=row.pop("price_version"),
            estimate_not_invoice=bool(row.pop("estimate_not_invoice")),
        )
        row["stream"] = bool(row["stream"])
        result.append(row)
    return result


def assert_storage_privacy(directory: Path, master: str) -> None:
    """Scan physical DB/WAL/SHM and optional debug output, not just query results."""
    sentinels = (
        SECRET,
        master,
        PROMPT,
        ANSWER,
        "synthetic reasoning",
        "sk-p1-client-override",
    )
    for path in (directory / "events").iterdir():
        raw = path.read_bytes()
        assert not any(
            s.encode(encoding) in raw
            for s in sentinels
            for encoding in ("utf8", "utf-16-le")
        )
    assert not (directory / "collector-status.json").exists()


@asynccontextmanager
async def proxy(
    upstream: str,
    label: str,
    explicit_false: bool = False,
    launch_script: str = "tests_p1/launch_contract.py",
    debug_jsonl: bool = False,
) -> AsyncIterator[tuple[str, str, Path, dict]]:
    """Start the actual launcher on an isolated port with synthetic credentials."""
    directory = RUN / label
    directory.mkdir(parents=True)
    config = directory / "config.yaml"
    config.write_text(
        "model_alias: deepseek\nprovider_model: deepseek/deepseek-chat\n"
        f"api_base: {upstream}/v1\nqueue_capacity: 8\n",
        encoding="utf8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        in {
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
    }
    env.update(
        {
            "UPSTREAM_API_KEY": SECRET,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "DATABASE_URL": "postgresql://p1-invalid-ignored",
            "LITELLM_MASTER_KEY": "sk-p1-env-must-be-ignored",
        }
    )
    if explicit_false:
        env["LOG_CONTENT"] = "false"
    proc = await asyncio.create_subprocess_exec(
        str(PROXY_PYTHON),
        "-I",
        "-S",
        str(ROOT / launch_script),
        "--offline",
        "--port",
        str(port),
        "--config",
        str(config),
        "--data-dir",
        str(directory),
        *(["--debug-jsonl"] if debug_jsonl else []),
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = asyncio.create_task(proc.communicate())
    url = f"http://127.0.0.1:{port}"
    diagnostic = {}
    master = ""
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            for _ in range(900):
                if proc.returncode is not None:
                    raw = (await output)[0].decode("utf8", "replace")
                    # The launcher emits only static codes, never vendor tracebacks.
                    print(raw.replace(SECRET, "[REDACTED]")[-1000:], flush=True)
                    raise RuntimeError("P1 launcher exited")
                try:
                    if (
                        await client.get(url + "/health/liveliness")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise TimeoutError("P1 launcher not ready")
        master = load_key(directory)
        assert master != "sk-p1-env-must-be-ignored"
        # The Windows venv launcher may spawn a base-python child: check our port.
        command = (
            f"Get-NetTCPConnection -State Listen -LocalPort {port} | "
            "Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress"
        )
        listeners = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if listeners.returncode == 0:
            data = json.loads(listeners.stdout.decode("utf8", "replace"))
            diagnostic["listeners"] = data if isinstance(data, list) else [data]
        else:
            print("listener query failed", listeners.returncode, flush=True)
        yield url, master, directory, diagnostic
    finally:
        if proc.returncode is None:
            proc.terminate()
        raw = (await asyncio.wait_for(output, 10))[0]
        diagnostic["credential_leak"] = any(
            s.encode() in raw for s in (SECRET, master) if s
        )
        diagnostic["content_leak"] = any(
            s.encode() in raw for s in (PROMPT, ANSWER, "synthetic reasoning")
        )


async def main(report_path: Path | None = None, *, p2_privacy: bool = False) -> dict:
    """Run TCP contracts with loopback fixtures and the unchanged dependencies."""
    RUN.mkdir(parents=True)
    logging.disable(logging.CRITICAL)
    fixture = Fixture()
    override_target = Fixture()
    results, evidence, diagnostics, security = [], [], [], []
    async with (
        serve(fixture.app) as upstream,
        serve(override_target.app) as override_url,
        httpx.AsyncClient(timeout=15, trust_env=False) as client,
    ):
        async with proxy(upstream, "default") as (url, master, directory, diagnostic):
            headers = {
                "Authorization": "Bearer " + master,
                "x-axi-client": "p1-client-a",
            }
            base = {
                "model": "deepseek",
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": 64,
            }
            # Rejected management requests must never reach the upstream.
            for name, supplied in [
                ("missing", {}),
                ("wrong", {"Authorization": "Bearer sk-wrong"}),
                (
                    "host-spoof",
                    {"Host": "localhost", "Authorization": "Bearer sk-wrong"},
                ),
                ("host-spoof-no-key", {"Host": "127.0.0.1:4000"}),
            ]:
                upstream_before = len(fixture.seen)
                events_before = len(load_events(directory))
                response = await client.post(
                    url + "/v1/chat/completions", json=base, headers=supplied
                )
                security.append(
                    {
                        "case": name,
                        "status": response.status_code,
                        "expected_status": 401,
                        "status_401_passed": response.status_code == 401,
                        "known_deviation": response.status_code != 401,
                        "passed": not 200 <= response.status_code < 300
                        and len(fixture.seen) == upstream_before,
                        "upstream_calls": len(fixture.seen) - upstream_before,
                    }
                )
                print("auth", name, response.status_code, flush=True)
                await asyncio.sleep(0.15)
                assert security[-1]["passed"]
                assert len(load_events(directory)) == events_before
            for path, method, body in [
                ("/key/info", "GET", None),
                ("/config/yaml", "GET", None),
                ("/key/generate", "POST", {}),
                ("/config/update", "POST", {}),
                ("/model/new", "POST", {}),
            ]:
                response = await client.request(method, url + path, json=body)
                security.append(
                    {
                        "path": path,
                        "credential": "absent",
                        "status": response.status_code,
                    }
                )
                print("management", path, response.status_code, flush=True)
                assert response.status_code in {400, 401, 403, 404, 405, 422, 500}, (
                    path,
                    response.status_code,
                )
            response = await client.get(url + "/key/info", headers=headers)
            security.append(
                {
                    "path": "/key/info",
                    "credential": "master",
                    "status": response.status_code,
                }
            )
            assert response.status_code in {
                400,
                404,
                500,
            }  # no DB, actual status recorded
            cases = [
                ("normal", False, False),
                ("normal", True, False),
                ("normal", False, True),
                ("normal", True, True),
                ("error429", False, False),
                ("error500", False, False),
                ("missing", False, False),
                ("missing", True, False),
                ("observer-fault", False, False),
                ("slow-sink", False, False),
                ("slow-client", True, False),
                ("abort-before", False, False),
                ("abort-before", True, False),
                ("abort-mid", True, False),
                ("spoof-disconnect", True, False),
                ("spoof-rejection", False, False),
            ]
            for mode, stream, compressed in cases:
                fixture.mode = (
                    "normal"
                    if mode
                    in {
                        "observer-fault",
                        "slow-sink",
                        "slow-client",
                        "spoof-disconnect",
                        "spoof-rejection",
                    }
                    else mode
                )
                fixture.compressed = compressed
                case = (
                    "p1-" + mode
                    if mode in {"observer-fault", "slow-sink"}
                    else f"p1-{mode}-{'s' if stream else 'n'}-"
                    f"{'gzip' if compressed else 'plain'}"
                )
                data = {
                    **base,
                    "stream": stream,
                    "metadata": {
                        "case_id": case,
                        "client_label": "p1-client-meta",
                        "usage_source": "reported",
                        "key_alias": "spoofed",
                        "private_credential": SECRET,
                        "private_content": PROMPT,
                    },
                }
                if stream:
                    data["stream_options"] = {"include_usage": True}
                if mode == "spoof-disconnect":
                    data["metadata"].update(
                        {
                            "client_disconnected": True,
                            "error_information": {
                                "error_code": "499",
                                "error_class": "ClientDisconnected",
                            },
                        }
                    )
                if mode == "spoof-disconnect":
                    data["standard_logging_object"] = {
                        "error_information": {
                            "error_code": "499",
                            "error_class": "ClientDisconnected",
                        }
                    }
                if mode == "spoof-rejection":
                    data["litellm_no_upstream_llm_call"] = True
                    fixture.mode = "error429"
                sent_headers = {
                    **headers,
                    "x-axi-client": "p1-slow-sink"
                    if mode == "slow-sink"
                    else "p1-client-a",
                }
                start = time.perf_counter()
                upstream_before = len(fixture.seen)
                status, received, error = None, b"", None
                events_before = len(load_events(directory))
                if mode == "abort-before":
                    fixture.started.clear()
                    task = asyncio.create_task(
                        client.post(
                            url + "/v1/chat/completions",
                            json=data,
                            headers=sent_headers,
                        )
                    )
                    await asyncio.wait_for(fixture.started.wait(), 5)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    await asyncio.sleep(2.5)
                else:
                    async with client.stream(
                        "POST",
                        url + "/v1/chat/completions",
                        json=data,
                        headers=sent_headers,
                    ) as response:
                        status = response.status_code
                        async for chunk in response.aiter_bytes():
                            received += chunk
                            if mode == "slow-client":
                                await asyncio.sleep(0.1)
                            if mode == "abort-mid":
                                break
                    elapsed = (time.perf_counter() - start) * 1000
                    if mode == "slow-sink":
                        assert elapsed < 1200
                    await asyncio.sleep(
                        2 if mode in {"slow-sink", "abort-mid"} else 0.3
                    )
                rows = load_events(directory)
                observations = [
                    r
                    for r in load_lines(directory / "disconnect-observations.jsonl")
                    if r["case"] == case
                ]
                expected = (
                    429
                    if mode == "spoof-rejection"
                    else (int(mode[-3:]) if mode.startswith("error") else 200)
                )
                if mode != "abort-before":
                    assert status == expected, (case, status)
                assert len(fixture.seen) - upstream_before == 1
                if expected == 200 and not mode.startswith("abort"):
                    assert ANSWER.encode() in received
                    if stream:
                        assert b"[DONE]" in received
                assert observations, case
                new_events = rows[events_before:]
                if mode != "observer-fault":
                    assert len(new_events) == 1, (case, len(new_events))
                    event = new_events[0]
                    disconnected = stream and mode in {"abort-before", "abort-mid"}
                    assert event["client_outcome"] == (
                        "client_disconnected" if disconnected else "unknown"
                    )
                    assert event["usage"]["usage_source"] == (
                        "estimated"
                        if disconnected
                        else "missing"
                        if expected >= 400
                        else "reported"
                        if not stream
                        and mode != "missing"
                        and (event["usage"]["prompt_tokens"] or 0) > 0
                        else "unknown"
                    )
                    if mode == "normal" and not stream:
                        assert event["usage"]["usage_source"] == "reported"
                        assert event["usage"]["total_tokens"] == 32
                    if mode == "missing" and not stream:
                        assert (
                            event["usage"]["prompt_tokens"]
                            == event["usage"]["total_tokens"]
                            == 0
                        )
                row = {
                    "case": case,
                    "mode": mode,
                    "stream": stream,
                    "gzip": compressed,
                    "http_status": status,
                    "done": b"[DONE]" in received,
                    "client_exception": error,
                    "request_ms": round(elapsed, 3) if mode != "abort-before" else None,
                    "upstream_attempts": len(fixture.seen) - upstream_before,
                    "event_outcomes": [
                        {
                            "client_outcome": e["client_outcome"],
                            "usage_source": e["usage"]["usage_source"],
                        }
                        for e in new_events
                    ],
                    "observations": observations,
                    "events_so_far": len(rows),
                }
                results.append(row)
                print(case, status, "observed", flush=True)
            fixture.mode, fixture.compressed = "normal", False
            for label in ("p1-client-a", "p1-client-b"):
                response = await client.post(
                    url + "/v1/chat/completions",
                    json={
                        **base,
                        "metadata": {
                            "case_id": "p1-label-" + label,
                            "client_label": "p1-conflict",
                        },
                    },
                    headers={**headers, "x-axi-client": label},
                )
                assert response.status_code == 200
            # Client credentials/base must not escape the configured upstream.
            before = len(fixture.seen)
            events_before = len(load_events(directory))
            response = await client.post(
                url + "/v1/chat/completions",
                headers=headers,
                json={
                    **base,
                    "api_key": "sk-p1-client-override",
                    "api_base": override_url + "/v1",
                    "metadata": {"case_id": "p1-override"},
                },
            )
            await asyncio.sleep(0.8)
            security.append(
                {
                    "case": "client-credential-override",
                    "status": response.status_code,
                    "expected_upstream_calls": len(fixture.seen) - before,
                    "override_target_calls": len(override_target.seen),
                    "passed": len(override_target.seen) == 0,
                }
            )
            print(
                "client override",
                response.status_code,
                "honeypot calls",
                len(override_target.seen),
                flush=True,
            )
            assert not override_target.seen
            assert not 200 <= response.status_code < 300 and len(fixture.seen) == before
            rows = load_events(directory)
            assert len(rows) == events_before
            assert len({r["litellm_call_id"] for r in rows}) == len(rows)
            assert any(
                r["client_label"]
                == {"header": "p1-client-a", "metadata": "p1-conflict"}
                for r in rows
            )
            assert any(
                r["client_label"]
                == {"header": "p1-client-b", "metadata": "p1-conflict"}
                for r in rows
            )
            assert all(
                r["client_outcome"] in {"unknown", "client_disconnected"}
                and r["key_alias"] is None
                for r in rows
            )
            assert all(
                r["usage"]["usage_source"]
                in {"reported", "unknown", "missing", "estimated"}
                for r in rows
            )
            assert all(
                r["usage"]["cache_hit_tokens"] is None
                and r["usage"]["reasoning_tokens"] is None
                for r in rows
                if r["usage"]["usage_source"] != "reported"
            )
            text = json.dumps(rows)
            assert not any(
                s in text
                for s in (
                    SECRET,
                    master,
                    PROMPT,
                    ANSWER,
                    "synthetic reasoning",
                    "spoofed",
                )
            )
            metrics = read_counters(directory / "events/events.sqlite3")
            assert metrics["projection_failed"] >= 1 and metrics["sink_failed"] == 0
            assert metrics["auth_rejected"] >= 5
            assert diagnostic.get("listeners") and all(
                r["LocalAddress"] == "127.0.0.1" for r in diagnostic["listeners"]
            )
            assert_storage_privacy(directory, master)
            evidence += rows
        diagnostics.append(diagnostic)
        async with proxy(upstream, "explicit-false", True, debug_jsonl=p2_privacy) as (
            url,
            master,
            directory,
            diagnostic,
        ):
            response = await client.post(
                url + "/v1/chat/completions",
                json={**base, "metadata": {"case_id": "p1-content-false"}},
                headers={"Authorization": "Bearer " + master},
            )
            assert response.status_code == 200
            await asyncio.sleep(0.8)
            rows = load_events(directory)
            assert len(rows) == 1
            if p2_privacy:
                for mode in ("normal", "error500", "abort-mid"):
                    fixture.mode = mode
                    stream = mode != "error500"
                    data = {
                        **base,
                        "stream": stream,
                        "metadata": {"case_id": "p1-privacy-" + mode},
                    }
                    if stream:
                        data["stream_options"] = {"include_usage": True}
                    async with client.stream(
                        "POST",
                        url + "/v1/chat/completions",
                        json=data,
                        headers={"Authorization": "Bearer " + master},
                    ) as response:
                        assert response.status_code == (
                            500 if mode == "error500" else 200
                        )
                        async for _ in response.aiter_bytes():
                            if mode == "abort-mid":
                                break
                    await asyncio.sleep(2 if mode == "abort-mid" else 0.4)
                rows = load_events(directory)
                assert len(rows) == 4
                assert rows[2]["provider_outcome"] == "error"
                assert rows[3]["client_outcome"] == "client_disconnected"
                assert rows[3]["usage"]["usage_source"] == "estimated"
                assert (
                    len(load_lines(directory / "events/development-events.jsonl")) == 4
                )
            text = json.dumps(rows)
            assert not any(
                s in text
                for s in (SECRET, master, PROMPT, ANSWER, "synthetic reasoning")
            )
            assert_storage_privacy(directory, master)
            evidence += rows
        diagnostics.append(diagnostic)
    assert all(not d["credential_leak"] and not d["content_leak"] for d in diagnostics)
    report = {
        "real_requests": 0,
        "fake_upstream_requests": len(fixture.seen),
        "passed": all(row.get("passed", True) for row in security),
        "cases": results,
        "events": evidence,
        "security": security,
        "diagnostics": diagnostics,
        "collector_metrics": metrics,
        "internal_errors": [
            r
            for r in load_lines(RUN / "default/disconnect-observations.jsonl")
            if r["case"] == "p1-internal-exception"
        ],
        "lock_sha256": hashlib.sha256(
            (ROOT / "spikes/litellm_p0/requirements-win-py313.lock.txt").read_bytes()
        ).hexdigest(),
    }
    # P1.3 preserves historical evidence and exports conclusions, never events/IDs.
    summary = {
        "real_requests": 0,
        "fake_upstream_requests": report["fake_upstream_requests"],
        "passed": report["passed"],
        "cases": [
            {
                "case": row["case"],
                "passed": True,
                "event_outcomes": row["event_outcomes"],
            }
            for row in results
        ],
        "security": security,
        "event_count": len(evidence),
        "auth_rejected": metrics["auth_rejected"],
        "privacy_passed": True,
        "database_privacy_matrix_passed": p2_privacy,
        "lock_sha256": report["lock_sha256"],
    }
    target = report_path or RUN / "contract-result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2), encoding="utf8")
    print(
        "P1 contracts recorded; real calls 0; passed=" + str(report["passed"]),
        flush=True,
    )
    if not report["passed"]:
        raise SystemExit(1)
    return summary


if __name__ == "__main__":
    asyncio.run(main())
