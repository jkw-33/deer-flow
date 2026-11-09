import subprocess
from threading import Thread

import httpx
import pytest

import ci_trace_runner
from app.gateway.ci_runner_client import runner_check


def test_runner_protocol(monkeypatch, tmp_path):
    token = "a" * 64
    file = tmp_path / "token"
    file.write_text(token, encoding="utf-8")
    monkeypatch.setenv("CI_TRACE_RUNNER_TOKEN_FILE", str(file))
    source = b"if true; then\r\n echo ok\r\nfi\r\n"

    def check(image, body):
        assert image == "fixture"
        if body == b"timeout":
            raise subprocess.TimeoutExpired("fixture", 8)
        assert body == source
        return 2, 4

    monkeypatch.setattr(ci_trace_runner, "bash_check", check)
    server = ci_trace_runner.create_server("127.0.0.1", 0, token, "fixture")
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert httpx.post(url + "/check", content=source).status_code == 401
        assert runner_check(url, source, "bash_syntax", 4) == (2, 4)
        with pytest.raises(httpx.HTTPStatusError) as timeout:
            runner_check(url, b"timeout", "bash_syntax", 4)
        assert timeout.value.response.status_code == 504
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()
    with pytest.raises(httpx.ConnectError):
        runner_check(url, source, "bash_syntax", 4)
