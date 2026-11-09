"""Owner-scoped CI Trace tasks and their resumable event stream."""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, SecretStr
from sse_starlette.sse import EventSourceResponse

from app.gateway.ci_actions_runner import actions_verify
from app.gateway.ci_actions_runner import configured as actions_configured
from app.gateway.ci_advice import repair_advice
from app.gateway.ci_evidence import GitHubCIEvidence, JobEvidence, PrivateRunError, annotation_evidence, parse_run_url
from app.gateway.ci_investigation import investigate
from app.gateway.ci_runner_client import runner_check
from app.gateway.ci_verification import MAX_SCRIPT_BYTES, candidate, node_target, verify
from app.gateway.csrf_middleware import is_allowed_auth_origin, is_secure_request
from app.gateway.github.app_auth import GitHubAppAuthError, mint_repository_read_token
from deerflow.config.credentials_key import CredentialsDecryptError, CredentialsKeyError, get_credentials_cipher
from deerflow.config.paths import get_paths

router = APIRouter(prefix="/api/ci-diagnoses", tags=["ci-trace"])
TERMINAL = {"completed", "failed"}
SESSION_COOKIE = "ci_trace_session"
SESSION_TTL = 24 * 60 * 60
MODEL_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "siliconflow": "https://api.siliconflow.cn/v1/chat/completions",
}
GITHUB_FLOW_COOKIE = "ci_trace_github_flow"


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


def _connect():
    path = get_paths().base_dir / "ci-trace.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE IF NOT EXISTS ci_diagnoses (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL,
        status TEXT NOT NULL, lease TEXT, lease_until REAL NOT NULL DEFAULT 0
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ci_model_settings (
        owner TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
        encrypted_key TEXT NOT NULL, expires_at INTEGER NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ci_github_connections (
        owner TEXT PRIMARY KEY, binding TEXT NOT NULL, login TEXT NOT NULL,
        encrypted_token TEXT NOT NULL, expires_at INTEGER NOT NULL
        )"""
    )
    return db


def _read_model(owner: str):
    with closing(_connect()) as db:
        return db.execute(
            "SELECT provider,model,encrypted_key FROM ci_model_settings WHERE owner=? AND expires_at>?",
            (owner, time.time()),
        ).fetchone()


def _save_model(owner: str, provider: str, model: str, key: str) -> None:
    encrypted_key = get_credentials_cipher().encrypt_text(key)
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM ci_model_settings WHERE expires_at<=?", (time.time(),))
        db.execute(
            """INSERT INTO ci_model_settings VALUES (?,?,?,?,?)
            ON CONFLICT(owner) DO UPDATE SET provider=excluded.provider,model=excluded.model,
            encrypted_key=excluded.encrypted_key,expires_at=excluded.expires_at""",
            (owner, provider, model, encrypted_key, int(time.time()) + SESSION_TTL),
        )


def _delete_model(owner: str) -> None:
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM ci_model_settings WHERE owner=?", (owner,))


def _github_config():
    client_id = os.getenv("CI_TRACE_GITHUB_CLIENT_ID")
    secret = os.getenv("CI_TRACE_GITHUB_CLIENT_SECRET")
    callback = os.getenv("CI_TRACE_GITHUB_CALLBACK_URL", "")
    parts = urlsplit(callback)
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"})
    if not client_id or not secret or not secure or parts.path != "/api/ci-diagnoses/github/callback" or parts.query or parts.fragment or parts.username:
        return None
    return client_id, secret, callback


def _github_connection(owner: str):
    with closing(_connect()) as db:
        return db.execute(
            "SELECT binding,login,encrypted_token FROM ci_github_connections WHERE owner=? AND expires_at>?",
            (owner, time.time()),
        ).fetchone()


def _save_github_connection(owner: str, login: str, token: str, ttl: int):
    encrypted = get_credentials_cipher().encrypt_text(token)
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM ci_github_connections WHERE expires_at<=?", (time.time(),))
        db.execute(
            "INSERT OR REPLACE INTO ci_github_connections VALUES (?,?,?,?,?)",
            (owner, uuid4().hex, login, encrypted, int(time.time()) + min(ttl, 8 * 60 * 60)),
        )


async def _private_token(task: dict, owner: str) -> str:
    connection = await asyncio.to_thread(_github_connection, owner)
    if not connection or connection["binding"] != task["github_binding"]:
        raise HTTPException(404, "Diagnosis not found")
    try:
        return await asyncio.to_thread(lambda: get_credentials_cipher().decrypt_text(connection["encrypted_token"]))
    except (CredentialsKeyError, CredentialsDecryptError) as error:
        raise HTTPException(404, "Diagnosis not found") from error


async def _verify_private_access(task: dict, owner: str):
    if not task.get("github_binding"):
        return
    token = await _private_token(task, owner)
    ref = parse_run_url(task["url"])
    path = ref.api_path + (f"/attempts/{task['attempt']}" if task["attempt"] else "")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await GitHubCIEvidence(client, token)._get(path)
    except httpx.HTTPError as error:
        raise HTTPException(404, "Diagnosis not found") from error


def _public_event(event: dict) -> dict:
    return {key: value for key, value in event.items() if key != "patch"}


def _public_task(task: dict) -> dict:
    return {
        **{key: value for key, value in task.items() if key not in {"github_binding", "events", "analysis"}},
        "private": bool(task.get("github_binding")),
        "analysis_started": bool(task.get("analysis")),
        "events": [_public_event(event) for event in task["events"]],
    }


def _model_endpoint(provider: str, base_url: str | None = None) -> str:
    if provider != "custom":
        if base_url:
            raise HTTPException(422, "Base URL is only supported for custom providers")
        return MODEL_ENDPOINTS[provider]
    base = (base_url or "").strip().rstrip("/")
    try:
        parts = urlsplit(base)
        valid = parts.scheme in {"http", "https"} and parts.hostname and parts.username is None and parts.port != 0 and not parts.query and not parts.fragment and not any(char.isspace() or char in "\\?#" for char in base)
    except ValueError:
        valid = False
    allowed = {entry.strip().rstrip("/") for entry in os.getenv("CI_TRACE_MODEL_BASE_URLS", "").split(",")}
    if not valid or base not in allowed:
        raise HTTPException(422, "Custom Base URL is not on the server allowlist")
    return base + "/chat/completions"


async def _model_message(provider: str, model: str, key: str, messages: list[dict], *, base_url: str | None = None, tool: dict | None = None) -> dict:
    endpoint = _model_endpoint(provider, base_url)
    payload = {"model": model, "messages": messages}
    if tool:
        payload.update(tools=[tool], tool_choice="auto" if tool["function"]["name"] == "read_log_excerpt" else "required")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload)
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            if tool and (message.get("tool_calls") or payload["tool_choice"] == "required"):
                return {"tool_calls": message.get("tool_calls")}
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty model response")
            return {"content": text.replace(key, "[redacted]")[:8000]}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise HTTPException(502, "Model provider could not complete the request") from error


async def _ask_model(provider: str, model: str, key: str, messages: list[dict], *, base_url: str | None = None) -> str:
    message = await _model_message(provider, model, key, messages, base_url=base_url)
    return message["content"]


def _create(owner, reference, github_binding=None, deep=False):
    task_id = str(uuid4())
    url = f"https://github.com/{reference.owner}/{reference.repository}/actions/runs/{reference.run_id}"
    task = {
        "id": task_id,
        "url": url,
        "repository": f"{reference.owner}/{reference.repository}",
        "attempt": reference.attempt,
        "status": "queued",
        "events": [{"seq": 1, "kind": "queued", "recorded_at": time.time()}],
        "expires_at": int(time.time()) + SESSION_TTL,
        "github_binding": github_binding,
        "deep": deep,
    }
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        db.execute("DELETE FROM ci_diagnoses WHERE json_extract(payload, '$.expires_at')<=?", (now,))
        active = db.execute(
            "SELECT COUNT(*) FROM ci_diagnoses WHERE owner=? AND (status='queued' OR (status='running' AND lease_until>?))",
            (owner, time.time()),
        ).fetchone()[0]
        if active >= 3:
            raise HTTPException(429, "Too many active diagnosis tasks")
        total = db.execute(
            "SELECT COUNT(*) FROM ci_diagnoses WHERE status='queued' OR (status='running' AND lease_until>?)",
            (now,),
        ).fetchone()[0]
        if total >= 12:
            raise HTTPException(429, "Diagnosis queue is full")
        db.execute("INSERT INTO ci_diagnoses (id,owner,payload,status) VALUES (?,?,?,?)", (task_id, owner, json.dumps(task), "queued"))
    return task


def _read(task_id, owner):
    with closing(_connect()) as db:
        row = db.execute("SELECT payload,lease_until FROM ci_diagnoses WHERE id=? AND owner=?", (task_id, owner)).fetchone()
    if row is None:
        raise HTTPException(404, "Diagnosis not found")
    task = json.loads(row["payload"])
    if task.get("expires_at", 0) <= time.time():
        raise HTTPException(404, "Diagnosis not found")
    return task, row["lease_until"]


def _claim(task_id, owner):
    lease = uuid4().hex
    with closing(_connect()) as db, db:
        changed = db.execute(
            """UPDATE ci_diagnoses SET status='running',lease=?,lease_until=?
            WHERE id=? AND owner=? AND (status='queued' OR (status='running' AND lease_until<?))""",
            (lease, time.time() + 150, task_id, owner, time.time()),
        ).rowcount
    return lease if changed else None


def _record(task_id, owner, lease, kind, **data):
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT payload FROM ci_diagnoses WHERE id=? AND owner=? AND lease=? AND lease_until>? AND status='running'",
            (task_id, owner, lease, time.time()),
        ).fetchone()
        if row is None:
            return
        task = json.loads(row["payload"])
        status = kind if kind in TERMINAL else "running"
        if kind == "attempt":
            if any(event["kind"] == "attempt" for event in task["events"]):
                return
            task["attempt"] = data["attempt"]
        if kind == "job" and any(event.get("job_id") == data["job_id"] for event in task["events"]):
            return
        if kind == "verification_started" and data.get("execution") == "github_actions":
            db.execute("CREATE TABLE IF NOT EXISTS ci_actions_admissions (task_id TEXT PRIMARY KEY, admitted_at REAL NOT NULL)")
            now = time.time()
            db.execute("DELETE FROM ci_actions_admissions WHERE admitted_at < ?", (now - 3600,))
            if db.execute("SELECT COUNT(*) FROM ci_actions_admissions").fetchone()[0] >= 6:
                return False
            if not db.execute("INSERT OR IGNORE INTO ci_actions_admissions VALUES (?, ?)", (task_id, now)).rowcount:
                return False
            db.execute("UPDATE ci_diagnoses SET lease_until=? WHERE id=?", (now + 310, task_id))
        task["status"] = status
        task["events"].append({"seq": len(task["events"]) + 1, "kind": kind, "recorded_at": time.time(), **data})
        db.execute("UPDATE ci_diagnoses SET payload=?,status=? WHERE id=?", (json.dumps(task), status, task_id))
        return True


async def _run(task_id: str, owner: str):
    lease = await asyncio.to_thread(_claim, task_id, owner)
    if lease is None:
        return
    task, _ = await asyncio.to_thread(_read, task_id, owner)
    url = task["url"] + (f"/attempts/{task['attempt']}" if task["attempt"] else "")

    async def emit(kind, **data):
        return await asyncio.to_thread(_record, task_id, owner, lease, kind, **data)

    async def on_attempt(run: dict, attempt: int):
        await emit("attempt", attempt=attempt, conclusion=run.get("conclusion"))

    async def on_job(evidence: JobEvidence):
        job = evidence.job
        await emit(
            "job",
            job_id=int(job["id"]),
            name=job.get("name", "")[:200],
            steps=[step["name"][:200] for step in job.get("steps", []) if step.get("conclusion") == "failure"][:20],
            log_state=evidence.log_state,
            checkout_shas=evidence.checkout_shas[:10],
            annotation_count=len(evidence.annotations),
            annotations=annotation_evidence(evidence.annotations, int(job["id"])) if not task.get("github_binding") else [],
            advice=repair_advice(evidence.log),
            log_excerpt=list(
                dict.fromkeys(
                    re.findall(
                        r"curl: \(22\) The requested URL returned error: [45][0-9]{2}|ERR_PNPM_OUTDATED_LOCKFILE|\bTS[0-9]{4}\b|\bMODULE_NOT_FOUND\b",
                        evidence.log or "",
                    )
                )
            )[:12]
            if not task.get("github_binding")
            else [],
            http_errors=sorted(set(re.findall(r"curl: \(22\) The requested URL returned error: ([45][0-9]{2})", evidence.log or ""))),
        )

    use_actions = not task.get("github_binding") and actions_configured(task["repository"])
    try:
        async with asyncio.timeout(430 if use_actions else 148), httpx.AsyncClient(timeout=12) as client:
            async with asyncio.timeout(120 if task.get("github_binding") or not os.getenv("CI_TRACE_RUNNER_URL") else 118):
                reference = parse_run_url(url)
                token = None
                private = bool(task.get("github_binding"))
                if private:
                    token = await _private_token(task, owner)
                elif os.getenv("GITHUB_APP_ID"):
                    token = await mint_repository_read_token(client, owner=reference.owner, repository=reference.repository)
                token = token or os.getenv("CI_TRACE_GITHUB_TOKEN")
                collector = GitHubCIEvidence(client, token)
                await emit(
                    "collection_started",
                    access="private_authorized" if private else "public_authenticated" if token else "public_anonymous",
                )
                evidence = await collector.collect(
                    reference,
                    public_only=not private,
                    on_attempt=on_attempt,
                    on_job=on_job,
                )
            runner_url = os.getenv("CI_TRACE_RUNNER_URL")
            already_verified = any(event["kind"] == "verification" for event in task["events"])
            if (runner_url or use_actions) and not private and not already_verified:
                for job in evidence.failed_jobs:
                    target = node_target(job.log, job.checkout_shas)
                    mode = "node_lookup" if target else "bash_syntax"
                    target = target or candidate(job.log, job.checkout_shas)
                    if target is None:
                        continue
                    sha, path, expected = target
                    context = {"sha": sha, "path": path, "mode": mode, "source_job": int(job.job["id"])}
                    if use_actions:
                        context["execution"] = "github_actions"
                    previous = any(event["kind"] == "verification_started" and event.get("execution") == "github_actions" for event in task["events"])
                    if previous or not await emit("verification_started", **context):
                        await emit("verification", status="unavailable", **context)
                        break
                    try:
                        async with asyncio.timeout(300 if use_actions else 28):
                            source = await collector.read_source(reference, sha, path, max_bytes=MAX_SCRIPT_BYTES)

                            def check(data):
                                return runner_check(runner_url, data, mode, expected)

                            if use_actions:

                                async def on_dispatch(run_id):
                                    await emit("verification_dispatched", run_id=run_id)

                                result = await actions_verify(client, task["repository"], sha, path, mode, expected, source, on_dispatch)
                            else:
                                result = await asyncio.to_thread(verify, source, path, expected, check, mode=mode)
                            result["source_sha256"] = hashlib.sha256(source).hexdigest()
                    except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError, TypeError):
                        result = {"status": "unavailable"}
                    await emit("verification", **context, **result)
                    break
                else:
                    await emit("verification_skipped", reason="no_supported_candidate" if evidence.failed_jobs else "no_failed_jobs")
            elif not already_verified:
                await emit("verification_skipped", reason="private_run" if private else "disabled")
            await emit("completed", failed_jobs=len(evidence.failed_jobs))
        if task.get("deep") and evidence.failed_jobs:
            try:
                await _start_analysis(task_id, owner)
            except HTTPException:
                pass
    except PrivateRunError:
        await emit("failed", reason="repository_unavailable")
    except HTTPException:
        await emit("failed", reason="repository_unavailable")
    except GitHubAppAuthError:
        await emit("failed", reason="github_app_configuration")
    except httpx.HTTPStatusError as error:
        response = error.response
        limited = response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
        await emit("failed", reason="rate_limited" if limited else "repository_unavailable")
    except (TimeoutError, httpx.RequestError):
        await emit("failed", reason="network_unavailable")
    except (KeyError, TypeError, ValueError):
        await emit("failed", reason="unexpected_response")


class CreateDiagnosis(BaseModel):
    url: str = Field(max_length=400)
    private: bool = False
    deep: bool = False


class ConfigureModel(BaseModel):
    provider: Literal["deepseek", "openai", "gemini", "openrouter", "siliconflow", "custom"]
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr
    base_url: str | None = Field(default=None, max_length=400)


class AnalyzeDiagnosis(BaseModel):
    task_id: UUID
    retry_after: int | None = Field(default=None, ge=1, le=10)


@router.get("/github")
async def github_status(request: Request, response: Response):
    token = _session_token(request)
    saved = await asyncio.to_thread(_github_connection, _session_owner(token)) if token else None
    response.headers["Cache-Control"] = "no-store"
    return {"available": _github_config() is not None, "login": saved["login"] if saved else None}


@router.post("/github")
async def connect_github(request: Request, response: Response):
    _check_origin(request)
    config = _github_config()
    if not config:
        raise HTTPException(503, "GitHub user authorization is not configured")
    if not is_secure_request(request) and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "HTTPS required")
    client_id, _, callback = config
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    token = _session_token(request) or secrets.token_urlsafe(32)
    _set_session_cookie(request, response, token)
    response.set_cookie(
        GITHUB_FLOW_COOKIE,
        f"{state}.{verifier}.{_session_owner(token)}",
        max_age=600,
        httponly=True,
        secure=is_secure_request(request),
        samesite="lax",
        path="/api/ci-diagnoses/github/callback",
    )
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "offline_access",
        }
    )
    return {"url": f"https://github.com/login/oauth/authorize?{query}"}


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = ""):
    failure = RedirectResponse("/diagnoses/github?result=failed", status_code=303)
    cookie_path = "/api/ci-diagnoses/github/callback"
    failure.delete_cookie(GITHUB_FLOW_COOKIE, path=cookie_path)
    failure.headers["Cache-Control"] = "no-store"
    token = _session_token(request)
    config = _github_config()
    flow = request.cookies.get(GITHUB_FLOW_COOKIE, "").split(".")
    if not token or not config or not code or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state) or len(flow) != 3:
        return failure
    expected, verifier, owner = flow
    if not expected.isascii() or not secrets.compare_digest(state, expected) or owner != _session_owner(token):
        return failure
    client_id, secret, callback = config
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            exchanged = await client.post(
                "https://github.com/login/oauth/access_token",
                data={"client_id": client_id, "client_secret": secret, "code": code, "redirect_uri": callback, "code_verifier": verifier},
                headers={"Accept": "application/json"},
            )
            exchanged.raise_for_status()
            result = exchanged.json()
            access_token, ttl = result["access_token"], int(result["expires_in"])
            if not access_token.startswith("ghu_") or ttl <= 0:
                return failure
            user = await client.get("https://api.github.com/user", headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"})
            user.raise_for_status()
            await asyncio.to_thread(_save_github_connection, owner, user.json()["login"], access_token, ttl)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, CredentialsKeyError):
        return failure
    success = RedirectResponse("/diagnoses/github?result=connected", status_code=303)
    success.delete_cookie(GITHUB_FLOW_COOKIE, path=cookie_path)
    success.headers["Cache-Control"] = "no-store"
    return success


@router.delete("/github", status_code=204)
async def disconnect_github(request: Request):
    _check_origin(request)
    token = _session_token(request)
    if token:

        def remove():
            with closing(_connect()) as db, db:
                db.execute("DELETE FROM ci_github_connections WHERE owner=?", (_session_owner(token),))

        await asyncio.to_thread(remove)


@router.get("/model")
async def model_status(request: Request, response: Response):
    token = _session_token(request)
    saved = await asyncio.to_thread(_read_model, _session_owner(token)) if token else None
    response.headers["Cache-Control"] = "no-store"
    return {"provider": saved["provider"], "model": saved["model"]} if saved else None


@router.put("/model")
async def configure_model(body: ConfigureModel, request: Request, response: Response):
    _check_origin(request)
    if not is_secure_request(request) and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "HTTPS required to configure a model")
    model, key = body.model.strip(), body.api_key.get_secret_value().strip()
    if not model or not key or len(key) > 512 or not key.isascii() or not key.isprintable():
        raise HTTPException(422, "Invalid model or API key")
    await _ask_model(body.provider, model, key, [{"role": "user", "content": "Reply with OK."}], base_url=body.base_url)
    token = _session_token(request) or secrets.token_urlsafe(32)
    try:
        credential = json.dumps({"key": key, "base_url": body.base_url}) if body.provider == "custom" else key
        await asyncio.to_thread(_save_model, _session_owner(token), body.provider, model, credential)
    except CredentialsKeyError as error:
        raise HTTPException(503, "Model credential storage is unavailable") from error
    _set_session_cookie(request, response, token)
    return {"provider": body.provider, "model": model}


@router.delete("/model", status_code=204)
async def clear_model(request: Request):
    _check_origin(request)
    token = _session_token(request)
    if token:
        await asyncio.to_thread(_delete_model, _session_owner(token))


_analysis_workers: set[asyncio.Task] = set()


def _analysis_event(task_id, owner, event, *, claim=False, attempt=1, retry_after=None):
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM ci_diagnoses WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        if row is None:
            return False
        task = json.loads(row["payload"])
        if task["expires_at"] <= time.time() or task.get("github_binding") or task["status"] != "completed":
            return False
        events = task.get("analysis", [])
        latest = events[-1] if events else {}
        current = latest.get("analysis_attempt", 1)
        if claim:
            if events and (latest["kind"] != "error" or retry_after != current or attempt != current + 1 or attempt > 10):
                return False
        elif not events or current != attempt or latest["kind"] in {"result", "error"}:
            return False
        task["analysis"] = [*events, {**event, "analysis_attempt": attempt, "recorded_at": time.time()}]
        db.execute("UPDATE ci_diagnoses SET payload=? WHERE id=?", (json.dumps(task), task_id))
        return True


async def _start_analysis(task_id, owner, retry_after=None):
    task, _ = await asyncio.to_thread(_read, task_id, owner)
    if task.get("github_binding"):
        raise HTTPException(404, "Diagnosis not found")
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
    saved = await asyncio.to_thread(_read_model, owner)
    if not saved:
        raise HTTPException(409, "Configure a model first")
    started = {"kind": "started", "agent": "ci-diagnosis", "provider": saved["provider"], "model": saved["model"]}
    if not await asyncio.to_thread(_analysis_event, task_id, owner, started, claim=True, attempt=attempt, retry_after=retry_after):
        return attempt

    async def worker():
        try:
            key = await asyncio.to_thread(lambda: get_credentials_cipher().decrypt_text(saved["encrypted_key"]))
            base_url = None
            if saved["provider"] == "custom":
                custom = json.loads(key)
                key, base_url = custom["key"], custom["base_url"]

            async def ask(messages, tool=None):
                return await _model_message(saved["provider"], saved["model"], key, messages, base_url=base_url, tool=tool)

            async def notify(item):
                await asyncio.to_thread(_analysis_event, task_id, owner, {"kind": "investigation", "item": item}, attempt=attempt)

            verifications = [event for event in task["events"] if event["kind"] == "verification"]
            async with asyncio.timeout(65):
                result = await investigate(jobs, verifications, ask, notify)
            terminal = {"kind": "result", **result}
        except TimeoutError:
            terminal = {"kind": "error", "reason": "timeout"}
        except (CredentialsKeyError, CredentialsDecryptError, HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
            terminal = {"kind": "error", "reason": "analysis_unavailable"}
        await asyncio.to_thread(_analysis_event, task_id, owner, terminal, attempt=attempt)

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
            task, _ = await asyncio.to_thread(_read, task_id, owner)
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
                await asyncio.to_thread(_analysis_event, task_id, owner, {"kind": "error", "reason": "interrupted"}, attempt=attempt)
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
    binding = None
    if body.private:
        connection = await asyncio.to_thread(_github_connection, owner)
        if not connection:
            raise HTTPException(403, "Connect GitHub to diagnose a private repository")
        binding = connection["binding"]
        await _verify_private_access(
            {"github_binding": binding, "url": f"https://github.com/{reference.owner}/{reference.repository}/actions/runs/{reference.run_id}", "attempt": reference.attempt},
            owner,
        )
    if body.deep and body.private:
        raise HTTPException(422, "Deep analysis is available for public diagnoses")
    if body.deep and not await asyncio.to_thread(_read_model, owner):
        raise HTTPException(409, "Configure a model first")
    task = await asyncio.to_thread(_create, owner, reference, binding, body.deep)
    _set_session_cookie(request, response, token)
    background.add_task(_run, task["id"], owner)
    return _public_task(task)


@router.get("/{task_id}")
async def diagnosis_snapshot(task_id: UUID, request: Request, response: Response, background: BackgroundTasks):
    owner = _require_owner(request)
    task, lease_until = await asyncio.to_thread(_read, str(task_id), owner)
    await _verify_private_access(task, owner)
    if task["status"] not in TERMINAL and lease_until <= time.time():
        background.add_task(_run, str(task_id), owner)
    response.headers["Cache-Control"] = "no-store"
    return _public_task(task)


@router.get("/{task_id}/events")
async def diagnosis_events(task_id: UUID, request: Request, after: int = 0):
    owner = _require_owner(request)
    task_id = str(task_id)
    task, _ = await asyncio.to_thread(_read, task_id, owner)
    await _verify_private_access(task, owner)
    last = request.headers.get("last-event-id", "")
    cursor = max(after, int(last) if last.isdecimal() and len(last) < 10 else 0)

    async def stream():
        nonlocal cursor
        next_auth_check = time.monotonic() + 15
        while not await request.is_disconnected():
            try:
                task, lease_until = await asyncio.to_thread(_read, task_id, owner)
                if task.get("github_binding"):
                    if task["events"][cursor:] or time.monotonic() >= next_auth_check:
                        await _verify_private_access(task, owner)
                        next_auth_check = time.monotonic() + 15
            except HTTPException:
                yield {"event": "access-lost", "data": "{}"}
                return
            for event in task["events"][cursor:]:
                cursor = event["seq"]
                yield {"event": "diagnosis", "id": str(cursor), "data": json.dumps(_public_event(event))}
            if task["status"] in TERMINAL:
                return
            if lease_until and lease_until < time.time():
                yield {"event": "recover", "data": "{}"}
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(stream(), headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{task_id}/artifacts/{artifact}")
async def diagnosis_artifact(task_id: UUID, artifact: Literal["report.txt", "repair.diff", "verification.log", "trace.json", "model-trace.json"], request: Request):
    owner = _require_owner(request)
    task, _ = await asyncio.to_thread(_read, str(task_id), owner)
    await _verify_private_access(task, owner)
    event = next((item for item in reversed(task["events"]) if item["kind"] == "verification"), None)
    if artifact == "model-trace.json":
        if not task.get("analysis"):
            raise HTTPException(404, "Artifact not found")
        content = (
            json.dumps(
                {"schema_version": 1, "scope": "model_analysis", "diagnosis_id": task["id"], "events": task["analysis"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    elif artifact == "trace.json":
        public = _public_task(task)
        content = json.dumps({"schema_version": 1, "scope": "diagnosis", **public}, ensure_ascii=False, indent=2) + "\n"
    elif artifact == "report.txt":
        if task["status"] != "completed":
            raise HTTPException(404, "Artifact not found")
        lines = [f"{task['url']} (attempt {task['attempt']})"]
        attempt = next((item for item in task["events"] if item["kind"] == "attempt"), {})
        lines.append(f"CI conclusion: {attempt.get('conclusion') or 'unknown'}")
        jobs = [item for item in task["events"] if item["kind"] == "job"]
        lines.append(f"Failed jobs: {len(jobs)}")
        for job in jobs:
            lines.extend(("", f"{job['name']} (job {job['job_id']})", f"Log evidence: {job['log_state']}"))
            lines.extend(f"Failed step: {step}" for step in job.get("steps", []))
            lines.extend(f"Checkout candidate: {sha}" for sha in job.get("checkout_shas", []))
            if job.get("annotation_count"):
                lines.append(f"Check annotations: {job['annotation_count']}")
            lines.extend(f"{item['id']} {item['path']}:{item['line']} {item['message']}" for item in job.get("annotations", []))
            lines.extend(f"HTTP error: {status}" for status in job.get("http_errors", []))
            lines.extend(f"Suggested next step: {advice}" for advice in job.get("advice", []))
        if event:
            lines.extend(("", f"{event['sha']}:{event['path']}", f"CRLF check ({event.get('mode', 'bash_syntax')}): {event['status']}", _verification_log(event)))
        content = "\n".join(lines) + "\n"
    elif artifact == "repair.diff" and event and "patch" in event:
        content = event["patch"]
    elif artifact == "verification.log" and event:
        content = _verification_log(event)
    else:
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


def _verification_log(event):
    node_lookup = event.get("mode") == "node_lookup"
    command = "/bin/bash --noprofile --norc -e -s (Node lookup with an empty module fixture)" if node_lookup else "/bin/bash --noprofile --norc -n -s"
    symptom = f"Original missing module with trailing CR: {event.get('missing_module', 'not recorded')}" if node_lookup else f"Original syntax error line: {event.get('error_line', 'not recorded')}"
    return (
        f"Check: Linux container {command}\n"
        f"Original source SHA-256: {event.get('source_sha256', 'not recorded')}\n"
        f"Original exit: {event.get('baseline', 'not recorded')}\n"
        f"{symptom}\n"
        f"LF-only rerun exit: {event.get('rerun', 'not recorded')}\n"
    )
