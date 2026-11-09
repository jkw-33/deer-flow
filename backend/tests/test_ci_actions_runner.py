import hashlib
import json

import httpx
import pytest

from app.gateway.ci_actions_runner import actions_verify


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_digest", [False, True])
async def test_dispatch_binds_job_result_to_source(tmp_path, monkeypatch, wrong_digest):
    source = b"node ./service.mjs\r\n"
    digest = hashlib.sha256(source).hexdigest()
    token = tmp_path / "token"
    token.write_text("scoped", encoding="utf-8")
    monkeypatch.setenv("CI_TRACE_ACTIONS_TOKEN_FILE", str(token))
    monkeypatch.setenv("CI_TRACE_ACTIONS_REPOSITORY", "operator/verifier")
    requests = []

    def handle(request):
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == "Bearer scoped"
        path = request.url.path
        requests.append(path)
        if path.endswith("/dispatches"):
            assert json.loads(request.content)["inputs"] == {
                "repository": "public/source",
                "sha": "a" * 40,
                "path": "check.sh",
                "mode": "node_lookup",
                "expected": "service.mjs",
                "source_sha256": digest,
            }
            return httpx.Response(200, json={"workflow_run_id": 12})
        if path.endswith("/runs/12"):
            return httpx.Response(200, json={"status": "completed", "conclusion": "success"})
        if path.endswith("/runs/12/attempts/1/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 22, "name": "verify", "conclusion": "success"}]})
        assert path.endswith("/jobs/22/logs")
        result = {"source_sha256": "wrong" if wrong_digest else digest, "status": "verified", "baseline": 1, "missing_module": "service.mjs", "rerun": 0}
        return httpx.Response(200, text="2026-01-01T00:00:00Z CI_TRACE_RESULT=" + json.dumps(result))

    async def dispatched(run_id):
        assert run_id == 12

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        call = actions_verify(client, "public/source", "a" * 40, "check.sh", "node_lookup", "service.mjs", source, dispatched)
        if wrong_digest:
            with pytest.raises(ValueError, match="source mismatch"):
                await call
        else:
            result = await call
            assert result["status"] == "verified"
            assert "-node ./service.mjs\r\n+node ./service.mjs\n" in result["patch"]
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_cancelled_poll_cancels_only_the_dispatched_run(tmp_path, monkeypatch):
    import asyncio

    token = tmp_path / "token"
    token.write_text("scoped", encoding="utf-8")
    monkeypatch.setenv("CI_TRACE_ACTIONS_TOKEN_FILE", str(token))
    monkeypatch.setenv("CI_TRACE_ACTIONS_REPOSITORY", "operator/verifier")
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/dispatches"):
            return httpx.Response(200, json={"workflow_run_id": 12})
        assert request.url.path == "/repos/operator/verifier/actions/runs/12/cancel"
        return httpx.Response(202)

    async def dispatched(_run_id):
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(asyncio.CancelledError):
            await actions_verify(client, "public/source", "a" * 40, "check.sh", "node_lookup", "service.mjs", b"node ./service.mjs\r\n", dispatched)
    assert len(requests) == 2
