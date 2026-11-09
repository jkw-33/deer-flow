import asyncio

import httpx
import pytest

from app.gateway.ci_evidence import GitHubCIEvidence, checkout_shas, parse_run_url

SHA = "5d62d6f284c4f45171c84198796ae550b8292a09"


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
            return httpx.Response(200, json={"run_attempt": 1, "conclusion": "failure"})
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

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await GitHubCIEvidence(client).collect(parse_run_url("https://github.com/pnpm/pnpm/actions/runs/42/attempts/1"))

    result = asyncio.run(run())
    assert result.attempt == 1
    assert result.failed_jobs[0].job["steps"] == [{"number": 5}]
    assert result.failed_jobs[0].log_state == "unavailable"
    assert result.failed_jobs[0].annotations[0]["message"] == "Process completed with exit code 22."
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
