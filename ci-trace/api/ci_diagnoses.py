"""HTTP routes and coordination for public CI diagnosis."""

import asyncio
import hashlib
import json
import re
import secrets
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sse_starlette.sse import EventSourceResponse

from api import ci_store as store
from api.ci_artifacts import public_event, public_task, render_artifact
from api.ci_evidence import parse_run_url
from api.ci_investigation import investigate
from api.ci_model import model_message
from api.ci_pipeline import run_diagnosis
from api.ci_store import TERMINAL
from api.settings import (
    SESSION_TTL,
    CredentialsDecryptError,
    CredentialsKeyError,
    get_credentials_cipher,
    is_allowed_auth_origin,
    is_loopback_origin,
    is_secure_request,
)

router = APIRouter(prefix="/api/ci-diagnoses", tags=["ci-trace"])
SESSION_COOKIE = "ci_trace_session"


def _session_token(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    return token if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None


def _session_owner(token: str) -> str:
    return "anonymous:" + hashlib.sha256(token.encode()).hexdigest()


def _require_owner(request: Request) -> str:
    token = _session_token(request)
    if token is None:
        raise HTTPException(404, "Diagnosis not found")
    return _session_owner(token)


def _check_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site") == "cross-site" or not is_allowed_auth_origin(request):
        raise HTTPException(403, "Cross-origin submission is not allowed")


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=is_secure_request(request), samesite="lax", path="/api/ci-diagnoses")
    response.headers["Cache-Control"] = "no-store"


async def _run(task_id: str, owner: str) -> None:
    if await run_diagnosis(task_id, owner):
        try:
            await _start_analysis(task_id, owner)
        except HTTPException:
            pass


class CreateDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=400)
    deep: bool = False


class ConfigureModel(BaseModel):
    provider: Literal["deepseek", "openai", "gemini", "openrouter", "siliconflow", "custom"]
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr
    base_url: str | None = Field(default=None, max_length=400)


class AnalyzeDiagnosis(BaseModel):
    task_id: UUID
    retry_after: int | None = Field(default=None, ge=1, le=10)


@router.get("/model")
async def model_status(request: Request, response: Response):
    token = _session_token(request)
    saved = await asyncio.to_thread(store.read_model, _session_owner(token)) if token else None
    response.headers["Cache-Control"] = "no-store"
    return {"provider": saved["provider"], "model": saved["model"]} if saved else None


@router.put("/model")
async def configure_model(body: ConfigureModel, request: Request, response: Response):
    _check_origin(request)
    if not is_secure_request(request) and not is_loopback_origin(request):
        raise HTTPException(403, "HTTPS required to configure a model")
    model, key = body.model.strip(), body.api_key.get_secret_value().strip()
    if not model or not key or len(key) > 512 or not key.isascii() or not key.isprintable():
        raise HTTPException(422, "Invalid model or API key")
    await model_message(body.provider, model, key, [{"role": "user", "content": "Reply with OK."}], base_url=body.base_url)
    token = _session_token(request) or secrets.token_urlsafe(32)
    try:
        credential = json.dumps({"key": key, "base_url": body.base_url}) if body.provider == "custom" else key
        await asyncio.to_thread(store.save_model, _session_owner(token), body.provider, model, credential)
    except CredentialsKeyError as error:
        raise HTTPException(503, "Model credential storage is unavailable") from error
    _set_session_cookie(request, response, token)
    return {"provider": body.provider, "model": model}


@router.delete("/model", status_code=204)
async def clear_model(request: Request):
    _check_origin(request)
    token = _session_token(request)
    if token:
        await asyncio.to_thread(store.delete_model, _session_owner(token))


_analysis_workers: set[asyncio.Task] = set()


async def _start_analysis(task_id, owner, retry_after=None):
    task, _ = await asyncio.to_thread(store.read, task_id, owner)
    jobs = [event for event in task["events"] if event["kind"] == "job"]
    if task["status"] != "completed" or not jobs:
        raise HTTPException(409, "No failed jobs available for analysis")
    analysis = task.get("analysis", [])
    latest = analysis[-1] if analysis else {}
    current = latest.get("analysis_attempt", 1)
    if analysis and (retry_after != current or latest["kind"] != "error"):
        return current
    attempt = current + 1 if analysis else 1
    if attempt > 10:
        raise HTTPException(409, "Analysis retry limit reached")
    saved = await asyncio.to_thread(store.read_model, owner)
    if not saved:
        raise HTTPException(409, "Configure a model first")
    started = {"kind": "started", "agent": "ci-diagnosis", "provider": saved["provider"], "model": saved["model"]}
    if not await asyncio.to_thread(store.analysis_event, task_id, owner, started, claim=True, attempt=attempt, retry_after=retry_after):
        return attempt

    async def worker():
        try:
            key = await asyncio.to_thread(lambda: get_credentials_cipher().decrypt_text(saved["encrypted_key"]))
            base_url = None
            if saved["provider"] == "custom":
                custom = json.loads(key)
                key, base_url = custom["key"], custom["base_url"]

            async def ask(messages, tool=None):
                return await model_message(saved["provider"], saved["model"], key, messages, base_url=base_url, tool=tool)

            async def notify(item):
                await asyncio.to_thread(store.analysis_event, task_id, owner, {"kind": "investigation", "item": item}, attempt=attempt)

            verifications = [event for event in task["events"] if event["kind"] == "verification"]
            async with asyncio.timeout(65):
                result = await investigate(jobs, verifications, ask, notify)
            terminal = {"kind": "result", **result}
        except TimeoutError:
            terminal = {"kind": "error", "reason": "timeout"}
        except (CredentialsKeyError, CredentialsDecryptError, HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
            terminal = {"kind": "error", "reason": "analysis_unavailable"}
        await asyncio.to_thread(store.analysis_event, task_id, owner, terminal, attempt=attempt)

    background = asyncio.create_task(worker())
    _analysis_workers.add(background)
    background.add_done_callback(_analysis_workers.discard)
    return attempt


@router.post("/model/analyze")
async def analyze_diagnosis(body: AnalyzeDiagnosis, request: Request, response: Response):
    _check_origin(request)
    owner = _require_owner(request)
    task_id = str(body.task_id)
    attempt = await _start_analysis(task_id, owner, body.retry_after)

    async def events():
        cursor = 0
        while True:
            task, _ = await asyncio.to_thread(store.read, task_id, owner)
            analysis = [event for event in task.get("analysis", []) if event.get("analysis_attempt", 1) == attempt]
            for event in analysis[cursor:]:
                yield event
                if event["kind"] in {"result", "error"}:
                    return
            cursor = len(analysis)
            if not analysis:
                yield {"kind": "error", "reason": "analysis_unavailable"}
                return
            if time.time() - analysis[0]["recorded_at"] > 75:
                await asyncio.to_thread(store.analysis_event, task_id, owner, {"kind": "error", "reason": "interrupted"}, attempt=attempt)
            await asyncio.sleep(0.25)

    if request.headers.get("accept") == "application/x-ndjson":

        async def stream():
            async for event in events():
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    response.headers["Cache-Control"] = "no-store"
    async for event in events():
        if event["kind"] == "result":
            return {key: value for key, value in event.items() if key not in {"kind", "recorded_at"}}
    raise HTTPException(502, "Model investigation could not complete")


@router.post("", status_code=202)
async def create_diagnosis(body: CreateDiagnosis, request: Request, response: Response, background: BackgroundTasks):
    _check_origin(request)
    try:
        reference = parse_run_url(body.url)
    except ValueError as error:
        raise HTTPException(422, "Expected a GitHub Actions run URL") from error
    token = _session_token(request) or secrets.token_urlsafe(32)
    owner = _session_owner(token)
    if body.deep and not await asyncio.to_thread(store.read_model, owner):
        raise HTTPException(409, "Configure a model first")
    task = await asyncio.to_thread(store.create, owner, reference, deep=body.deep)
    _set_session_cookie(request, response, token)
    background.add_task(_run, task["id"], owner)
    return public_task(task)


@router.get("/{task_id}")
async def diagnosis_snapshot(task_id: UUID, request: Request, response: Response, background: BackgroundTasks):
    owner = _require_owner(request)
    task, lease_until = await asyncio.to_thread(store.read, str(task_id), owner)
    if task["status"] not in TERMINAL and lease_until <= time.time():
        background.add_task(_run, str(task_id), owner)
    response.headers["Cache-Control"] = "no-store"
    return public_task(task)


@router.get("/{task_id}/events")
async def diagnosis_events(task_id: UUID, request: Request, after: int = 0):
    owner = _require_owner(request)
    task_id = str(task_id)
    await asyncio.to_thread(store.read, task_id, owner)
    last = request.headers.get("last-event-id", "")
    cursor = max(after, int(last) if last.isdecimal() and len(last) < 10 else 0)

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                task, lease_until = await asyncio.to_thread(store.read, task_id, owner)
            except HTTPException:
                yield {"event": "access-lost", "data": "{}"}
                return
            for event in task["events"][cursor:]:
                cursor = event["seq"]
                yield {"event": "diagnosis", "id": str(cursor), "data": json.dumps(public_event(event))}
            if task["status"] in TERMINAL:
                return
            if lease_until <= time.time():
                yield {"event": "recover", "data": "{}"}
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(stream(), headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{task_id}/artifacts/{artifact}")
async def diagnosis_artifact(task_id: UUID, artifact: Literal["report.txt", "repair.diff", "verification.log", "trace.json", "model-trace.json"], request: Request):
    owner = _require_owner(request)
    task, _ = await asyncio.to_thread(store.read, str(task_id), owner)
    content = render_artifact(task, artifact)
    if content is None:
        raise HTTPException(404, "Artifact not found")
    return Response(
        content,
        media_type="application/json" if artifact.endswith(".json") else "text/plain",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{artifact}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
