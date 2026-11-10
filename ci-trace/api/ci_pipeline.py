"""Collect a pinned public run and optionally verify its first supported candidate."""

import asyncio
import hashlib
import os

import httpx
from fastapi import HTTPException

from api import ci_store as store
from api.ci_evidence import (
    GitHubCIEvidence,
    JobEvidence,
    PrivateRunError,
    RunPendingError,
    job_event_data,
    parse_run_url,
)
from api.ci_runner_client import runner_check
from api.ci_verification import MAX_SCRIPT_BYTES, candidate, node_target, verify


async def run_diagnosis(task_id: str, owner: str) -> bool:
    lease = await asyncio.to_thread(store.claim, task_id, owner)
    if lease is None:
        return False
    task, _ = await asyncio.to_thread(store.read, task_id, owner)
    url = task["url"] + (f"/attempts/{task['attempt']}" if task["attempt"] else "")

    async def emit(kind, **data):
        return await asyncio.to_thread(store.record, task_id, owner, lease, kind, **data)

    async def on_attempt(run: dict, attempt: int):
        await emit("attempt", attempt=attempt, conclusion=run.get("conclusion"))

    runner_url = os.getenv("CI_TRACE_RUNNER_URL")
    events = task["events"]
    recorded_jobs = [event for event in events if event["kind"] == "job"]
    recorded_job_ids = frozenset(event["job_id"] for event in recorded_jobs)
    already_verified = any(event["kind"] == "verification" for event in events)
    selected = next((event["verification_candidate"] for event in recorded_jobs if "verification_candidate" in event), None)

    async def on_job(evidence: JobEvidence):
        nonlocal selected
        data = job_event_data(evidence)
        if selected is None and not already_verified:
            target = node_target(evidence.log, evidence.checkout_shas)
            mode = "node_lookup" if target else "bash_syntax"
            target = target or candidate(evidence.log, evidence.checkout_shas)
            if target:
                sha, path, expected = target
                data["verification_candidate"] = {"sha": sha, "path": path, "mode": mode, "source_job": data["job_id"], "expected": expected}
        if await emit("job", **data) and "verification_candidate" in data:
            selected = data["verification_candidate"]

    try:
        async with asyncio.timeout(150), httpx.AsyncClient(timeout=12) as client:
            reference = parse_run_url(url)
            token = os.getenv("CI_TRACE_GITHUB_TOKEN")
            collector = GitHubCIEvidence(client, token)
            if not any(event["kind"] == "collection_started" for event in events):
                await emit("collection_started", access="public_authenticated" if token else "public_anonymous")
            async with asyncio.timeout(118 if runner_url else 120):
                failed_jobs = await collector.collect(reference, on_attempt=on_attempt, on_job=on_job, recorded_job_ids=recorded_job_ids)
            if runner_url and selected and not already_verified:
                context = {key: value for key, value in selected.items() if key != "expected"}
                started = any(event["kind"] == "verification_started" and event.get("source_job") == selected["source_job"] for event in events)
                if not started and not await emit("verification_started", **context):
                    return False
                try:
                    async with asyncio.timeout(28):
                        source = await collector.read_source(reference, selected["sha"], selected["path"], max_bytes=MAX_SCRIPT_BYTES)

                        def check(data):
                            return runner_check(runner_url, data, selected["mode"], selected["expected"])

                        result = await asyncio.to_thread(verify, source, selected["path"], selected["expected"], check, mode=selected["mode"])
                        result["source_sha256"] = hashlib.sha256(source).hexdigest()
                except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError, TypeError):
                    result = {"status": "unavailable"}
                await emit("verification", **context, **result)
            elif not already_verified and not any(event["kind"] == "verification_skipped" for event in events):
                reason = "disabled" if not runner_url else "no_supported_candidate" if failed_jobs else "no_failed_jobs"
                await emit("verification_skipped", reason=reason)
            if not await emit("completed", failed_jobs=failed_jobs):
                return False
        return bool(task.get("deep") and failed_jobs)
    except RunPendingError as error:
        await emit("run_pending", github_status=error.status)
    except (PrivateRunError, HTTPException):
        await emit("failed", reason="repository_unavailable")
    except httpx.HTTPStatusError as error:
        response = error.response
        limited = response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
        await emit("failed", reason="rate_limited" if limited else "repository_unavailable")
    except (TimeoutError, httpx.RequestError):
        await emit("failed", reason="network_unavailable")
    except (KeyError, TypeError, ValueError):
        await emit("failed", reason="unexpected_response")
    return False
