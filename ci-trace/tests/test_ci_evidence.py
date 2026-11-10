import asyncio

import httpx
import pytest

from api.ci_evidence import (
    GitHubCIEvidence,
    JobEvidence,
    PrivateRunError,
    checkout_shas,
    job_event_data,
    parse_run_url,
)

SHA = "5d62d6f284c4f45171c84198796ae550b8292a09"


def test_metadata_retries_transport_and_gateway_errors(monkeypatch):
    replies = iter([httpx.ConnectError("temporary failure"), httpx.Response(502), httpx.Response(200, json={"run_attempt": 1})])
    delays = []

    def respond(request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("api.ci_evidence.asyncio.sleep", sleep)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await GitHubCIEvidence(client)._get("/test")

    assert asyncio.run(run()) == {"run_attempt": 1}
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize(("status", "headers"), [(403, {"x-ratelimit-remaining": "0"}), (429, {}), (503, {"retry-after": "10"})])
def test_metadata_does_not_retry_rate_limit_or_retry_after(status, headers):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers=headers)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await GitHubCIEvidence(client)._get("/test")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert len(requests) == 1


def test_job_event_keeps_ordered_markers_and_http_statuses():
    http_504 = "curl: (22) The requested URL returned error: 504"
    http_403 = "curl: (22) The requested URL returned error: 403"
    evidence = JobEvidence(
        job={"id": 42},
        log="\n".join(("prefixTS1234", http_504, "error TS1234:", http_504, "MODULE_NOT_FOUND", http_403)),
        log_state="available",
        checkout_shas=(),
    )

    public = job_event_data(evidence)
    assert public["log_excerpt"] == [http_504, "TS1234", "MODULE_NOT_FOUND", http_403]
    assert public["http_errors"] == ["403", "504"]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.test/pnpm/pnpm/actions/runs/42",
        "https://github.com/pnpm/../actions/runs/42",
        "https://github.com/pnpm/pnpm/actions/runs/42/attempts/0",
    ],
)
def test_rejects_invalid_run_urls(url):
    with pytest.raises(ValueError):
        parse_run_url(url)


def test_checkout_requires_git_log_command():
    assert checkout_shas(f"2026-11-09T08:06:52Z {SHA}") == ()
    assert checkout_shas(f"[command]/usr/local/bin/git log -1 --format=%H\r\n2026-11-09T08:06:52Z {SHA}\r\n") == (SHA,)


def test_explicit_attempt_keeps_its_failed_job_when_log_is_forbidden():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/attempts/1"):
            return httpx.Response(200, json={"run_attempt": 1, "status": "completed", "conclusion": "failure", "repository": {"private": False}})
        if request.url.path.endswith("/attempts/1/jobs"):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 23,
                            "conclusion": "failure",
                            "steps": [{"number": 5}],
                            "check_run_url": "https://api.github.com/repos/pnpm/pnpm/check-runs/24",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/check-runs/24/annotations"):
            return httpx.Response(200, json=[{"message": "Process completed with exit code 22."}])
        return httpx.Response(403)

    jobs = []

    async def on_job(evidence):
        jobs.append(evidence)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await GitHubCIEvidence(client).collect(
                parse_run_url("https://github.com/pnpm/pnpm/actions/runs/42/attempts/1"), on_job=on_job
            )

    assert asyncio.run(run()) == 1
    assert jobs[0].job["steps"] == [{"number": 5}]
    assert jobs[0].log_state == "unavailable"
    assert jobs[0].annotations[0]["message"] == "Process completed with exit code 22."
    assert paths[-2].endswith("/actions/jobs/23/logs")
    assert not any(path.endswith("/runs/42") for path in paths)


def test_log_redirect_does_not_forward_credentials():
    def handle(request):
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer gh-test"
            return httpx.Response(302, headers={"Location": "https://pipelines.actions.githubusercontent.com/log"})
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, content=b"failed\r\n")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), cookies={"session": "test"}) as client:
            return await GitHubCIEvidence(client, "gh-test")._read_log("/test")

    assert asyncio.run(run()) == ("failed\r\n", "available")


@pytest.mark.parametrize("repository", [None, {}, {"private": True}, {"private": "false"}])
def test_unconfirmed_public_visibility_blocks_collection_even_with_a_token(repository):
    def handle(request):
        assert request.url.path.endswith("/attempts/1")
        return httpx.Response(200, json={"run_attempt": 1, "repository": repository})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await GitHubCIEvidence(client, "token").collect(parse_run_url("https://github.com/acme/demo/actions/runs/42/attempts/1"))

    with pytest.raises(PrivateRunError):
        asyncio.run(run())


@pytest.mark.parametrize(
    ("location", "state"),
    [
        ("https://pipelines.actions.githubusercontent.com:bad/log", "unavailable"),
        ("https://pipelines.actions.githubusercontent.com.attacker.test/log", "unsupported_redirect"),
    ],
)
def test_malformed_or_lookalike_log_redirect_is_skipped(location, state):
    def handle(request):
        assert request.url.host == "api.github.com"
        return httpx.Response(302, headers={"Location": location})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await GitHubCIEvidence(client, "token")._read_log("/test")

    assert asyncio.run(run()) == (None, state)


def test_public_annotation_evidence_bounds_and_redacts():
    from api.ci_evidence import annotation_evidence

    annotations = ({"path": "src/check.py", "start_line": 12, "message": "failed at https://user:pass@example.test?key=hidden\nAPI key: do-not-expose"},)
    assert annotation_evidence(annotations, 42) == [{"id": "job-42-annotation-1", "path": "src/check.py", "line": 12, "message": "failed at [url] [redacted]"}]
