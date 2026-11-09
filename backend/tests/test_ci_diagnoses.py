import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.ci_advice import repair_advice
from app.gateway.ci_evidence import parse_run_url
from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.github import app_auth
from app.gateway.routers import ci_diagnoses
from deerflow.config.credentials_key import CredentialsCipher


def test_model_endpoints_use_presets_or_an_exact_allowlisted_base(monkeypatch):
    monkeypatch.setenv("CI_TRACE_MODEL_BASE_URLS", "https://gateway.example/v1, http://host.docker.internal:11434/v1")
    assert ci_diagnoses._model_endpoint("siliconflow") == "https://api.siliconflow.cn/v1/chat/completions"
    assert ci_diagnoses._model_endpoint("gemini").endswith("/openai/chat/completions")
    assert ci_diagnoses._model_endpoint("openrouter") == "https://openrouter.ai/api/v1/chat/completions"
    assert ci_diagnoses._model_endpoint("custom", "https://gateway.example/v1/") == "https://gateway.example/v1/chat/completions"
    assert ci_diagnoses._model_endpoint("custom", "http://host.docker.internal:11434/v1").endswith("/v1/chat/completions")
    for provider, base_url in (
        ("custom", "https://gateway.example/v2"),
        ("custom", "http://169.254.169.254"),
        ("custom", "https://gateway.example/v1?x=y"),
        ("openai", "https://gateway.example/v1"),
    ):
        with pytest.raises(HTTPException) as error:
            ci_diagnoses._model_endpoint(provider, base_url)
        assert error.value.status_code == 422
    monkeypatch.delenv("CI_TRACE_MODEL_BASE_URLS")
    with pytest.raises(HTTPException):
        ci_diagnoses._model_endpoint("custom", "https://gateway.example/v1")


def test_rule_advice_does_not_infer_root_cause_without_logs():
    assert "完整日志" in repair_advice(None)[0]
    assert "冻结" not in repair_advice("Install Vale")[0]
    assert "pnpm-lock.yaml" in repair_advice("ERR_PNPM_OUTDATED_LOCKFILE")[0]
    assert "网络路径" in repair_advice("curl: (22) The requested URL returned error: 504")[0]


def test_model_key_is_encrypted_session_scoped_and_clearable(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    cipher = CredentialsCipher.from_keys([Fernet.generate_key()])
    monkeypatch.setattr(ci_diagnoses, "get_credentials_cipher", lambda: cipher)

    async def reply(provider, model, key, messages, *, base_url=None):
        assert (provider, model, key) == ("deepseek", "test-model", "test-key")
        assert base_url is None
        return "OK"

    monkeypatch.setattr(ci_diagnoses, "_ask_model", reply)
    app = FastAPI()
    app.include_router(ci_diagnoses.router)
    path = "/api/ci-diagnoses/model"
    with TestClient(app, base_url="https://example.test") as browser:
        saved = browser.put(path, json={"provider": "deepseek", "model": "test-model", "api_key": "test-key"})
        assert saved.json() == {"provider": "deepseek", "model": "test-model"}
        assert browser.get(path).json() == saved.json()
        assert b"test-key" not in (tmp_path / "ci-trace.sqlite3").read_bytes()
        with TestClient(app, base_url="https://example.test") as other:
            assert other.get(path).json() is None
        assert browser.delete(path).status_code == 204
        assert browser.get(path).json() is None


def test_github_start_binds_pkce_and_rejects_mismatched_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    monkeypatch.setenv("CI_TRACE_GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("CI_TRACE_GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CI_TRACE_GITHUB_CALLBACK_URL", "https://example.test/api/ci-diagnoses/github/callback")
    app = FastAPI()
    app.include_router(ci_diagnoses.router)
    with TestClient(app, base_url="https://example.test") as browser:
        start = browser.post("/api/ci-diagnoses/github")
        assert start.status_code == 200
        url = urlsplit(start.json()["url"])
        query = parse_qs(url.query)
        assert url.netloc == "github.com"
        assert query["code_challenge_method"] == ["S256"]
        assert "test-secret" not in start.text
        callback = browser.get(
            "/api/ci-diagnoses/github/callback",
            params={"state": "a" * 43, "code": "invalid"},
            follow_redirects=False,
        )
        assert callback.headers["location"] == "/diagnoses/github?result=failed"


def test_private_task_disallows_model_analysis_and_requires_bound_github_access(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    token = "a" * 43
    owner = ci_diagnoses._session_owner(token)
    task = ci_diagnoses._create(owner, parse_run_url("https://github.com/acme/private/actions/runs/42"), "old-binding")
    app = FastAPI()
    app.include_router(ci_diagnoses.router)
    with TestClient(app, base_url="https://example.test") as browser:
        browser.cookies.set(ci_diagnoses.SESSION_COOKIE, token)
        path = f"/api/ci-diagnoses/{task['id']}"
        assert browser.get(path).status_code == 404
        assert browser.get(f"{path}/events").status_code == 404
        assert browser.post("/api/ci-diagnoses/model/analyze", json={"task_id": task["id"]}).status_code == 404


def test_github_user_token_authorizes_private_task_and_revocation_blocks_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    cipher = CredentialsCipher.from_keys([Fernet.generate_key()])
    monkeypatch.setattr(ci_diagnoses, "get_credentials_cipher", lambda: cipher)
    monkeypatch.setenv("CI_TRACE_GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("CI_TRACE_GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CI_TRACE_GITHUB_CALLBACK_URL", "https://example.test/api/ci-diagnoses/github/callback")
    allowed = True
    original_client = httpx.AsyncClient

    def handle(request):
        if request.url.path == "/login/oauth/access_token":
            fields = parse_qs(request.content.decode())
            assert fields["code"] == ["mock-code"]
            assert fields["code_verifier"]
            return httpx.Response(200, json={"access_token": "ghu_test", "expires_in": 28800})
        assert request.headers["authorization"] == "Bearer ghu_test"
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "test-user"})
        assert request.url.path == "/repos/acme/private/actions/runs/42"
        return httpx.Response(200 if allowed else 404, json={})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))

    async def complete(task_id, owner):
        lease = ci_diagnoses._claim(task_id, owner)
        ci_diagnoses._record(task_id, owner, lease, "completed", failed_jobs=0)

    monkeypatch.setattr(ci_diagnoses, "_run", complete)
    app = FastAPI()
    app.include_router(ci_diagnoses.router)
    with TestClient(app, base_url="https://example.test") as browser:
        start = browser.post("/api/ci-diagnoses/github").json()
        state = parse_qs(urlsplit(start["url"]).query)["state"][0]
        callback = browser.get(
            "/api/ci-diagnoses/github/callback",
            params={"state": state, "code": "mock-code"},
            follow_redirects=False,
        )
        assert callback.headers["location"] == "/diagnoses/github?result=connected"
        created = browser.post("/api/ci-diagnoses", json={"url": "https://github.com/acme/private/actions/runs/42", "private": True})
        assert created.status_code == 202
        task = created.json()
        assert task["private"] is True
        assert "github_binding" not in task
        path = f"/api/ci-diagnoses/{task['id']}"
        assert browser.get(path).status_code == 200
        allowed = False
        assert browser.get(path).status_code == 404
        assert browser.get(f"{path}/events").status_code == 404


def test_anonymous_browser_can_submit_and_replay_but_cannot_read_another_session(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))

    async def complete(task_id, owner):
        lease = ci_diagnoses._claim(task_id, owner)
        ci_diagnoses._record(task_id, owner, lease, "completed", failed_jobs=0)

    monkeypatch.setattr(ci_diagnoses, "_run", complete)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.include_router(ci_diagnoses.router)
    body = {"url": "https://github.com/acme/demo/actions/runs/42"}
    with TestClient(app, base_url="https://example.test") as browser:
        blocked = browser.post("/api/ci-diagnoses", json=body, headers={"Origin": "https://other.test"})
        assert blocked.status_code == 403
        created = browser.post("/api/ci-diagnoses", json=body, headers={"Origin": "https://example.test"})
        assert created.status_code == 202
        assert "HttpOnly" in created.headers["set-cookie"]
        assert "Secure" in created.headers["set-cookie"]
        path = f"/api/ci-diagnoses/{created.json()['id']}"
        assert browser.get(path).json()["status"] == "completed"
        assert "event: diagnosis" in browser.get(f"{path}/events?after=1").text
        trace = browser.get(f"{path}/artifacts/trace.json").json()
        assert trace["schema_version"] == 1
        assert trace["scope"] == "diagnosis"
        assert [event["kind"] for event in trace["events"]] == ["queued", "completed"]
        assert all(event["recorded_at"] > 0 for event in trace["events"])
        assert "github_binding" not in trace
        report = browser.get(f"{path}/artifacts/report.txt")
        assert report.status_code == 200
        assert "Failed jobs: 0" in report.text
        assert browser.get(f"{path}/artifacts/repair.diff").status_code == 404
        with TestClient(app, base_url="https://example.test") as other:
            assert other.get(f"{path}/artifacts/trace.json").status_code == 404
            assert other.get(f"{path}/artifacts/report.txt").status_code == 404
            assert other.get(path).status_code == 404
            assert other.get(f"{path}/events").status_code == 404


@pytest.mark.parametrize("runner_error", [None, httpx.ConnectError, httpx.ReadTimeout])
def test_verification_artifacts_follow_diagnosis_ownership(tmp_path, monkeypatch, runner_error):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))

    monkeypatch.setenv("CI_TRACE_RUNNER_URL", "http://runner.test")
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    sha = "a" * 40
    source = b"if true; then\r\n echo ok\r\nfi\r\n"

    class Collector:
        def __init__(self, *_):
            pass

        async def collect(self, reference, *, public_only, on_attempt, on_job):
            assert public_only
            await on_attempt({"conclusion": "failure"}, 1)
            job = ci_diagnoses.JobEvidence({"id": 10}, "./check.sh: line 4: syntax error: unexpected end of file", "available", (sha,))
            await on_job(job)
            return SimpleNamespace(failed_jobs=[job])

        async def read_source(self, reference, commit, path, *, max_bytes):
            assert (commit, path) == (sha, "check.sh")
            return source

    monkeypatch.setattr(ci_diagnoses, "GitHubCIEvidence", Collector)

    def check(_url, data, *_):
        if runner_error:
            raise runner_error("Runner unavailable")
        return (2, 4) if data == source else (0, None)

    monkeypatch.setattr(ci_diagnoses, "runner_check", check)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.include_router(ci_diagnoses.router)
    body = {"url": "https://github.com/acme/demo/actions/runs/42"}
    with TestClient(app, base_url="https://example.test") as browser:
        created = browser.post("/api/ci-diagnoses", json=body, headers={"Origin": "https://example.test"})
        assert created.status_code == 202
        path = f"/api/ci-diagnoses/{created.json()['id']}"
        snapshot = browser.get(path).json()
        assert snapshot["status"] == "completed"
        event = next(event for event in snapshot["events"] if event["kind"] == "verification")
        if runner_error:
            assert event["status"] == "unavailable"
            assert browser.get(f"{path}/artifacts/repair.diff").status_code == 404
            return
        assert event["status"] == "verified"
        assert "patch" not in event
        stream = browser.get(f"{path}/events").text
        assert '"kind": "verification"' in stream
        assert '"patch"' not in stream
        artifact = f"{path}/artifacts/repair.diff"
        response = browser.get(artifact)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-disposition"] == 'attachment; filename="repair.diff"'
        assert b"-if true; then\r\n" in response.content
        assert "LF-only rerun exit: 0" in browser.get(f"{path}/artifacts/verification.log").text
        assert f"{sha}:check.sh" in browser.get(f"{path}/artifacts/report.txt").text
        with TestClient(app, base_url="https://example.test") as other:
            assert other.get(artifact).status_code == 404


def test_github_app_token_is_scoped_to_repository_and_read_permissions(monkeypatch):
    monkeypatch.setattr(app_auth, "mint_app_jwt", lambda: "app-test-jwt")

    def handle(request):
        assert request.headers["authorization"] == "Bearer app-test-jwt"
        if request.method == "GET":
            assert request.url.path == "/repos/acme/demo/installation"
            return httpx.Response(200, json={"id": 42, "suspended_at": None})
        assert request.url.path == "/app/installations/42/access_tokens"
        import json

        assert json.loads(request.content) == {
            "repositories": ["demo"],
            "permissions": {"actions": "read", "checks": "read", "contents": "read"},
        }
        return httpx.Response(201, json={"token": "installation-test-token"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await app_auth.mint_repository_read_token(client, owner="acme", repository="demo")

    assert asyncio.run(run()) == "installation-test-token"


def test_github_app_missing_or_suspended_installation_never_mints_token(monkeypatch):
    monkeypatch.setattr(app_auth, "mint_app_jwt", lambda: "app-test-jwt")

    async def run(status, data):
        def handle(request):
            assert request.method == "GET"
            return httpx.Response(status, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await app_auth.mint_repository_read_token(client, owner="acme", repository="demo")

    assert asyncio.run(run(404, {})) is None
    assert asyncio.run(run(200, {"id": 42, "suspended_at": "2026-11-09"})) is None


def test_task_keeps_its_owner_cursor_and_pinned_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    task = ci_diagnoses._create("owner", parse_run_url("https://github.com/pnpm/pnpm/actions/runs/42"))
    task_id = task["id"]
    lease = ci_diagnoses._claim(task_id, "owner")
    assert lease is not None
    assert ci_diagnoses._claim(task_id, "owner") is None
    with pytest.raises(HTTPException) as error:
        ci_diagnoses._read(task_id, "other")
    assert error.value.status_code == 404

    ci_diagnoses._record(task_id, "owner", lease, "attempt", attempt=2)
    ci_diagnoses._record(task_id, "owner", lease, "job", job_id=23)
    ci_diagnoses._record(task_id, "owner", lease, "job", job_id=23)
    ci_diagnoses._record(task_id, "owner", "stale-lease", "failed")
    ci_diagnoses._record(task_id, "owner", lease, "completed", failed_jobs=1)

    saved, _ = ci_diagnoses._read(task_id, "owner")
    assert saved["attempt"] == 2
    assert saved["status"] == "completed"
    assert [(event["seq"], event["kind"]) for event in saved["events"]] == [
        (1, "queued"),
        (2, "attempt"),
        (3, "job"),
        (4, "completed"),
    ]


def test_analysis_claim_is_session_scoped_and_persists_terminal_event(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    task = ci_diagnoses._create("owner", parse_run_url("https://github.com/acme/demo/actions/runs/42"), deep=True)
    task_id = task["id"]
    lease = ci_diagnoses._claim(task_id, "owner")
    ci_diagnoses._record(task_id, "owner", lease, "completed", failed_jobs=1)
    started = {"kind": "started", "agent": "ci-diagnosis"}
    assert not ci_diagnoses._analysis_event(task_id, "other", started, claim=True)
    assert ci_diagnoses._analysis_event(task_id, "owner", started, claim=True)
    assert not ci_diagnoses._analysis_event(task_id, "owner", started, claim=True)
    assert ci_diagnoses._analysis_event(task_id, "owner", {"kind": "result", "text": "result"})
    assert not ci_diagnoses._analysis_event(task_id, "owner", {"kind": "error"})
    saved, _ = ci_diagnoses._read(task_id, "owner")
    assert [event["kind"] for event in saved["analysis"]] == ["started", "result"]
    assert ci_diagnoses._public_task(saved)["analysis_started"]
    assert "analysis" not in ci_diagnoses._public_task(saved)


def test_retry_claim_preserves_history_and_rejects_duplicate_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_diagnoses, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    task = ci_diagnoses._create("owner", parse_run_url("https://github.com/acme/demo/actions/runs/42"))
    task_id = task["id"]
    lease = ci_diagnoses._claim(task_id, "owner")
    ci_diagnoses._record(task_id, "owner", lease, "completed", failed_jobs=1)
    started = {"kind": "started"}
    assert ci_diagnoses._analysis_event(task_id, "owner", started, claim=True)
    assert ci_diagnoses._analysis_event(task_id, "owner", {"kind": "error", "reason": "timeout"})
    assert ci_diagnoses._analysis_event(task_id, "owner", started, claim=True, attempt=2, retry_after=1)
    assert not ci_diagnoses._analysis_event(task_id, "owner", started, claim=True, attempt=2, retry_after=1)
    assert not ci_diagnoses._analysis_event(task_id, "owner", {"kind": "error"}, attempt=1)
    assert ci_diagnoses._analysis_event(task_id, "owner", {"kind": "result"}, attempt=2)
    saved, _ = ci_diagnoses._read(task_id, "owner")
    assert [(event["analysis_attempt"], event["kind"]) for event in saved["analysis"]] == [(1, "started"), (1, "error"), (2, "started"), (2, "result")]
