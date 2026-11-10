import asyncio
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from api import ci_pipeline
from api import ci_store as store
from api.ci_artifacts import public_task
from api.ci_evidence import parse_run_url
from api.main import app

RUN = "https://github.com/acme/demo/actions/runs/42"
SHA = "a" * 40


def use_github_mock(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(ci_pipeline.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("status", ["queued", "in_progress", "completed"])
def test_run_status_distinguishes_pending_from_completed_without_failures(tmp_path, monkeypatch, status):
    monkeypatch.setenv("CI_TRACE_DATABASE", str(tmp_path / "trace.sqlite3"))
    monkeypatch.delenv("CI_TRACE_RUNNER_URL", raising=False)
    paths = []

    def github(request):
        path = request.url.path
        paths.append(path)
        if path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 10, "conclusion": "success"}]})
        assert path.endswith("/attempts/2")
        return httpx.Response(200, json={"run_attempt": 2, "status": status, "conclusion": "success" if status == "completed" else None, "repository": {"private": False}})

    use_github_mock(monkeypatch, github)
    with TestClient(app) as browser:
        created = browser.post("/api/ci-diagnoses", json={"url": RUN + "/attempts/2"})
        assert created.status_code == 202
        endpoint = f"/api/ci-diagnoses/{created.json()['id']}"
        saved = browser.get(endpoint).json()
        report = browser.get(endpoint + "/artifacts/report.txt")
        assert report.status_code == 200
        assert saved["attempt"] == 2
        if status == "completed":
            assert saved["status"] == "completed"
            assert "Failed jobs: 0" in report.text
            assert paths[-1].endswith("/jobs")
        else:
            assert saved["status"] == "run_pending"
            assert saved["events"][-1]["github_status"] == status
            assert not any(path.endswith("/jobs") for path in paths)
            assert f"CI status: {status}" in report.text
            assert "Failed jobs:" not in report.text
            assert '"kind": "run_pending"' in browser.get(endpoint + "/events").text


def test_interrupted_run_resumes_pinned_attempt_and_reuses_first_candidate(tmp_path, monkeypatch):
    db_path = tmp_path / "trace.sqlite3"
    monkeypatch.setenv("CI_TRACE_DATABASE", str(db_path))
    monkeypatch.setenv("CI_TRACE_RUNNER_URL", "http://runner.test")
    log_reads = []
    latest_reads = 0
    interrupted = True
    source = b"if true; then\r\n echo ok\r\nfi\r\n"

    def github(request):
        nonlocal latest_reads
        path = request.url.path
        if path.endswith("/runs/42"):
            latest_reads += 1
            return httpx.Response(200, json={"run_attempt": 2 if interrupted else 3})
        if path.endswith("/attempts/2"):
            return httpx.Response(200, json={"run_attempt": 2, "status": "completed", "conclusion": "failure", "repository": {"private": False}})
        if path.endswith("/attempts/2/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": job, "conclusion": "failure"} for job in (10, 11)]})
        if path.endswith("/jobs/10/logs"):
            log_reads.append(10)
            return httpx.Response(200, text=f"[command]/usr/bin/git log -1 --format=%H\n{SHA}\n./check.sh: line 4: syntax error: unexpected end of file")
        if path.endswith("/jobs/11/logs"):
            log_reads.append(11)
            if interrupted:
                raise asyncio.CancelledError
            return httpx.Response(200, text="no supported candidate")
        if path.endswith("/contents/check.sh"):
            assert request.url.params["ref"] == SHA
            return httpx.Response(200, content=source)
        raise AssertionError(path)

    use_github_mock(monkeypatch, github)

    def check(_url, data, mode, expected):
        assert (mode, expected) == ("bash_syntax", 4)
        return (2, 4) if data == source else (0, None)

    monkeypatch.setattr(ci_pipeline, "runner_check", check)
    task = store.create("owner", parse_run_url(RUN))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ci_pipeline.run_diagnosis(task["id"], "owner"))
    checkpoint, _ = store.read(task["id"], "owner")
    assert checkpoint["attempt"] == 2
    assert checkpoint["events"][-1]["verification_candidate"]["sha"] == SHA
    assert "verification_candidate" not in public_task(checkpoint)["events"][-1]

    interrupted = False
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE ci_diagnoses SET lease_until=0 WHERE id=?", (task["id"],))
    asyncio.run(ci_pipeline.run_diagnosis(task["id"], "owner"))
    saved, _ = store.read(task["id"], "owner")
    assert saved["attempt"] == 2
    assert latest_reads == 1
    assert log_reads == [10, 11, 11]
    assert [event["kind"] for event in saved["events"]] == ["queued", "collection_started", "attempt", "job", "job", "verification_started", "verification", "completed"]
    assert saved["events"][-2]["status"] == "verified"
    assert saved["events"][-1]["failed_jobs"] == 2
