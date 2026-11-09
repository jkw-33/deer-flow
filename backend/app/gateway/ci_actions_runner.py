"""Submit one verification to the operator's dedicated GitHub Actions repository."""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import httpx

from app.gateway.ci_evidence import API_ROOT, GitHubCIEvidence
from app.gateway.ci_verification import verify


def configured(source_repo):
    allowlist = os.getenv("CI_TRACE_ACTIONS_SOURCE_REPOS", "").lower().split(",")
    return bool(os.getenv("CI_TRACE_ACTIONS_REPOSITORY") and source_repo.lower() in {repo.strip() for repo in allowlist})


async def actions_verify(client, source_repo, sha, path, mode, expected, source, on_dispatch):
    repo = os.environ["CI_TRACE_ACTIONS_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo) or repo.split("/")[1] in {".", ".."}:
        raise ValueError("Invalid verification repository")
    token = Path(os.getenv("CI_TRACE_ACTIONS_TOKEN_FILE", "")).read_text(encoding="utf-8").strip()
    api = GitHubCIEvidence(client, token)
    api.headers["X-GitHub-Api-Version"] = "2026-03-10"
    prefix = f"/repos/{repo}/actions"
    digest = hashlib.sha256(source).hexdigest()
    response = await client.post(
        f"{API_ROOT}{prefix}/workflows/ci-trace-verify.yml/dispatches",
        headers=api.headers,
        json={
            "ref": os.getenv("CI_TRACE_ACTIONS_REF", "main"),
            "inputs": {
                "repository": source_repo,
                "sha": sha,
                "path": path,
                "mode": mode,
                "expected": str(expected),
                "source_sha256": digest,
            },
        },
        follow_redirects=False,
    )
    response.raise_for_status()
    run_id = response.json().get("workflow_run_id")
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Missing verification run ID")
    run_path = f"{prefix}/runs/{run_id}"
    try:
        await on_dispatch(run_id)
        while True:
            run = await api._get(run_path)
            if run["status"] == "completed":
                break
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(4):
                await client.post(f"{API_ROOT}{run_path}/cancel", headers=api.headers, follow_redirects=False)
        except (TimeoutError, httpx.HTTPError):
            pass
        raise
    if run["conclusion"] != "success":
        raise ValueError("Verification workflow did not complete")
    jobs = await api._get(f"{run_path}/attempts/1/jobs")
    candidates = [job for job in jobs["jobs"] if job["name"] == "verify" and job["conclusion"] == "success"]
    if len(candidates) != 1:
        raise ValueError("Missing verification job")
    log, _ = await api._read_log(f"{prefix}/jobs/{int(candidates[0]['id'])}/logs")
    lines = re.findall(r"(?m)^(?:\S+Z )?CI_TRACE_RESULT=(\{[^\r\n]{1,1024}\})\r?$", log or "")
    if len(lines) != 1:
        raise ValueError("Missing verification result")
    result = json.loads(lines[0])
    if not isinstance(result, dict) or result.get("source_sha256") != digest:
        raise ValueError("Verification source mismatch")
    if result.get("status") != "verified":
        return {"status": "unconfirmed"}
    symptom_key, baseline = ("error_line", 2) if mode == "bash_syntax" else ("missing_module", 1)
    if (result.get("baseline"), result.get(symptom_key), result.get("rerun")) != (baseline, expected, 0):
        raise ValueError("Invalid verification result")
    checks = iter([(baseline, expected), (0, None)])
    return verify(source, path, expected, lambda _: next(checks), mode=mode)
