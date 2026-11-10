import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import ci_diagnoses, ci_pipeline
from api import ci_store as store
from api.ci_advice import repair_advice
from api.ci_artifacts import public_task
from api.ci_evidence import parse_run_url
from api.ci_model import model_endpoint
from api.settings import CredentialsCipher


def test_model_endpoints_use_presets_or_an_exact_allowlisted_base(monkeypatch):
    monkeypatch.setenv("CI_TRACE_MODEL_BASE_URLS", "https://gateway.example/v1, http://host.docker.internal:11434/v1")
    assert model_endpoint("siliconflow") == "https://api.siliconflow.cn/v1/chat/completions"
    assert model_endpoint("gemini").endswith("/openai/chat/completions")
    assert model_endpoint("openrouter") == "https://openrouter.ai/api/v1/chat/completions"
    assert model_endpoint("custom", "https://gateway.example/v1/") == "https://gateway.example/v1/chat/completions"
    assert model_endpoint("custom", "http://host.docker.internal:11434/v1").endswith("/v1/chat/completions")
    for provider, base_url in (
        ("custom", "https://gateway.example/v2"),
        ("custom", "http://169.254.169.254"),
        ("custom", "https://gateway.example/v1?x=y"),
        ("openai", "https://gateway.example/v1"),
    ):
        with pytest.raises(HTTPException) as error:
            model_endpoint(provider, base_url)
        assert error.value.status_code == 422
    monkeypatch.delenv("CI_TRACE_MODEL_BASE_URLS")
    with pytest.raises(HTTPException):
        model_endpoint("custom", "https://gateway.example/v1")


def test_rule_advice_does_not_infer_root_cause_without_logs():
    assert "完整日志" in repair_advice(None)[0]
    assert "冻结" not in repair_advice("Install Vale")[0]
    assert "pnpm-lock.yaml" in repair_advice("ERR_PNPM_OUTDATED_LOCKFILE")[0]
    assert "网络路径" in repair_advice("curl: (22) The requested URL returned error: 504")[0]


def test_model_key_is_encrypted_session_scoped_and_clearable(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))
    cipher = CredentialsCipher.from_keys([Fernet.generate_key()])
    monkeypatch.setattr(store, "get_credentials_cipher", lambda: cipher)

    async def reply(provider, model, key, messages, *, base_url=None):
        assert (provider, model, key) == ("deepseek", "test-model", "test-key")
        assert base_url is None
        return {"content": "OK"}

    monkeypatch.setattr(ci_diagnoses, "model_message", reply)
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


def test_anonymous_browser_can_submit_and_replay_but_cannot_read_another_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))

    async def complete(task_id, owner):
        lease = store.claim(task_id, owner)
        store.record(task_id, owner, lease, "completed", failed_jobs=0)

    monkeypatch.setattr(ci_diagnoses, "_run", complete)
    app = FastAPI()
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
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))

    monkeypatch.setenv("CI_TRACE_RUNNER_URL", "http://runner.test")
    sha = "a" * 40
    source = b"if true; then\r\n echo ok\r\nfi\r\n"

    class Collector:
        def __init__(self, *_):
            pass

        async def collect(self, reference, *, on_attempt, on_job, recorded_job_ids):
            assert not recorded_job_ids
            await on_attempt({"conclusion": "failure"}, 1)
            job = ci_pipeline.JobEvidence({"id": 10}, "./check.sh: line 4: syntax error: unexpected end of file", "available", (sha,))
            await on_job(job)
            return 1

        async def read_source(self, reference, commit, path, *, max_bytes):
            assert (commit, path) == (sha, "check.sh")
            return source

    monkeypatch.setattr(ci_pipeline, "GitHubCIEvidence", Collector)

    def check(_url, data, *_):
        if runner_error:
            raise runner_error("Runner unavailable")
        return (2, 4) if data == source else (0, None)

    monkeypatch.setattr(ci_pipeline, "runner_check", check)
    app = FastAPI()
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


def test_task_keeps_its_owner_cursor_and_pinned_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))
    task = store.create("owner", parse_run_url("https://github.com/pnpm/pnpm/actions/runs/42"))
    task_id = task["id"]
    lease = store.claim(task_id, "owner")
    assert lease is not None
    assert store.claim(task_id, "owner") is None
    with pytest.raises(HTTPException) as error:
        store.read(task_id, "other")
    assert error.value.status_code == 404

    store.record(task_id, "owner", lease, "attempt", attempt=2)
    store.record(task_id, "owner", lease, "job", job_id=23)
    store.record(task_id, "owner", lease, "job", job_id=23)
    store.record(task_id, "owner", "stale-lease", "failed")
    store.record(task_id, "owner", lease, "completed", failed_jobs=1)

    saved, _ = store.read(task_id, "owner")
    assert saved["attempt"] == 2
    assert saved["status"] == "completed"
    assert [(event["seq"], event["kind"]) for event in saved["events"]] == [
        (1, "queued"),
        (2, "attempt"),
        (3, "job"),
        (4, "completed"),
    ]


def test_analysis_claim_is_session_scoped_and_persists_terminal_event(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))
    task = store.create("owner", parse_run_url("https://github.com/acme/demo/actions/runs/42"), deep=True)
    task_id = task["id"]
    lease = store.claim(task_id, "owner")
    store.record(task_id, "owner", lease, "completed", failed_jobs=1)
    started = {"kind": "started", "agent": "ci-diagnosis"}
    assert not store.analysis_event(task_id, "other", started, claim=True)
    assert store.analysis_event(task_id, "owner", started, claim=True)
    assert not store.analysis_event(task_id, "owner", started, claim=True)
    assert store.analysis_event(task_id, "owner", {"kind": "result", "text": "result"})
    assert not store.analysis_event(task_id, "owner", {"kind": "error"})
    saved, _ = store.read(task_id, "owner")
    assert [event["kind"] for event in saved["analysis"]] == ["started", "result"]
    snapshot = public_task(saved)
    assert snapshot["analysis_started"]
    assert "analysis" not in snapshot


def test_retry_claim_preserves_history_and_rejects_duplicate_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "ci-trace.sqlite3"))
    task = store.create("owner", parse_run_url("https://github.com/acme/demo/actions/runs/42"))
    task_id = task["id"]
    lease = store.claim(task_id, "owner")
    store.record(task_id, "owner", lease, "completed", failed_jobs=1)
    started = {"kind": "started"}
    assert store.analysis_event(task_id, "owner", started, claim=True)
    assert store.analysis_event(task_id, "owner", {"kind": "error", "reason": "timeout"})
    assert store.analysis_event(task_id, "owner", started, claim=True, attempt=2, retry_after=1)
    assert not store.analysis_event(task_id, "owner", started, claim=True, attempt=2, retry_after=1)
    assert not store.analysis_event(task_id, "owner", {"kind": "error"}, attempt=1)
    assert store.analysis_event(task_id, "owner", {"kind": "result"}, attempt=2)
    saved, _ = store.read(task_id, "owner")
    assert [(event["analysis_attempt"], event["kind"]) for event in saved["analysis"]] == [(1, "started"), (1, "error"), (2, "started"), (2, "result")]
