import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api import ci_diagnoses
from api import ci_store as store
from api.main import app
from api.settings import get_credentials_cipher


def test_standalone_replays_model_analysis_after_an_explicit_retry(tmp_path, monkeypatch):
    origin = "https://ci.example.test"
    monkeypatch.setenv("CI_TRACE_ORIGIN", origin)
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "trace.sqlite3"))
    monkeypatch.setenv("CI_TRACE_CREDENTIALS_KEY", Fernet.generate_key().decode())
    model_available = False

    async def collect(task_id, owner):
        lease = store.claim(task_id, owner)
        store.record(task_id, owner, lease, "job", job_id=42)
        store.record(task_id, owner, lease, "completed", failed_jobs=1)

    async def model(_provider, _model, key, messages, *, base_url=None, tool=None):
        assert key == "test-key"
        if messages[-1]["content"] == "Reply with OK.":
            return {"content": "OK"}
        if not model_available:
            raise ValueError("Mock provider unavailable")
        if tool:
            return {"tool_calls": [{"id": "read", "function": {"name": "read_job_evidence"}}]}
        evidence = json.loads(messages[-1]["content"])
        assert evidence["id"] == "job-42"
        return {"content": json.dumps({"findings": [{"kind": "observation", "text": "Job failed", "evidence_ids": [evidence["id"]]}]})}

    monkeypatch.setattr(ci_diagnoses, "_run", collect)
    monkeypatch.setattr(ci_diagnoses, "model_message", model)
    api = "/api/ci-diagnoses"
    headers = {"Origin": origin}
    with TestClient(app, base_url=origin) as browser:
        assert browser.get("/health").json() == {"status": "ok"}
        config = {"provider": "deepseek", "model": "test-model", "api_key": "test-key"}
        assert browser.put(f"{api}/model", json=config, headers={"Origin": "https://foreign.test"}).status_code == 403
        saved = browser.put(f"{api}/model", json=config, headers=headers)
        assert saved.status_code == 200
        assert "Secure" in saved.headers["set-cookie"]
        created = browser.post(api, json={"url": "https://github.com/acme/demo/actions/runs/42"}, headers=headers)
        assert created.status_code == 202
        task_id = created.json()["id"]
        headers["Accept"] = "application/x-ndjson"
        analysis = f"{api}/model/analyze"
        first = browser.post(analysis, json={"task_id": task_id}, headers=headers)
        assert json.loads(first.text.splitlines()[-1])["kind"] == "error"

        model_available = True
        retried = browser.post(analysis, json={"task_id": task_id, "retry_after": 1}, headers=headers)
        result = json.loads(retried.text.splitlines()[-1])
        assert result["analysis_attempt"] == 2
        assert result["findings"][0]["evidence_ids"] == ["job-42"]
        replay = browser.post(analysis, json={"task_id": task_id}, headers=headers)
        assert replay.text == retried.text
        trace = browser.get(f"{api}/{task_id}/artifacts/model-trace.json").json()
        assert {event["analysis_attempt"] for event in trace["events"]} == {1, 2}


def test_invalid_model_request_never_echoes_credentials():
    with TestClient(app) as browser:
        secret = "sensitive-model-key"
        result = browser.put("/api/ci-diagnoses/model", json={"provider": "bad", "api_key": {"invalid": secret}})
        assert result.status_code == 422
        assert secret not in result.text
        result = browser.post("/api/ci-diagnoses", json={"url": "https://github.com/acme/demo/actions/runs/42", "private": True})
        assert result.status_code == 422


def test_proxy_loopback_model_configuration_creates_one_persistent_key(tmp_path, monkeypatch):
    origin = "http://127.0.0.1:3300"
    key_file = tmp_path / "credentials.key"
    monkeypatch.setenv("CI_TRACE_ORIGIN", origin)
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "trace.sqlite3"))
    monkeypatch.setenv("CI_TRACE_CREDENTIALS_KEY_FILE", str(key_file))
    monkeypatch.delenv("CI_TRACE_CREDENTIALS_KEY", raising=False)

    with ThreadPoolExecutor(max_workers=4) as executor:
        encrypted = list(executor.map(lambda _: get_credentials_cipher().encrypt_text("secret"), range(4)))
    assert all(get_credentials_cipher().decrypt_text(item) == "secret" for item in encrypted)
    assert list(tmp_path.iterdir()) == [key_file]

    async def model(*_args, **_kwargs):
        return {"content": "OK"}

    monkeypatch.setattr(ci_diagnoses, "model_message", model)
    with TestClient(app, base_url="http://api:8001") as browser:
        config = {"provider": "deepseek", "model": "test-model", "api_key": "test-key"}
        result = browser.put("/api/ci-diagnoses/model", json=config, headers={"Origin": origin})
        assert result.status_code == 200
        assert "Secure" not in result.headers["set-cookie"]
        assert browser.get("/api/ci-diagnoses/model").json()["model"] == "test-model"


def test_legacy_private_records_stay_inaccessible_and_unclaimed(tmp_path, monkeypatch):
    db_path = tmp_path / "trace.sqlite3"
    monkeypatch.setenv("CI_TRACE_DATABASE", str(db_path))
    token = "a" * 43
    owner = ci_diagnoses._session_owner(token)
    task = store.create(owner, ci_diagnoses.parse_run_url("https://github.com/acme/demo/actions/runs/42"))
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE ci_diagnoses SET payload=json_set(payload, '$.github_binding', 'legacy') WHERE id=?", (task["id"],))
    assert store.claim(task["id"], owner) is None
    with TestClient(app, cookies={ci_diagnoses.SESSION_COOKIE: token}) as browser:
        url = f"/api/ci-diagnoses/{task['id']}"
        for endpoint in (url, url + "/events", url + "/artifacts/trace.json"):
            assert browser.get(endpoint).status_code == 404


def test_unclaimed_stream_requests_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "trace.sqlite3"))
    token = "a" * 43
    owner = ci_diagnoses._session_owner(token)
    task = store.create(owner, ci_diagnoses.parse_run_url("https://github.com/acme/demo/actions/runs/42"))
    with TestClient(app, cookies={ci_diagnoses.SESSION_COOKIE: token}) as browser:
        stream = browser.get(f"/api/ci-diagnoses/{task['id']}/events?after=1")
        assert "event: recover" in stream.text
