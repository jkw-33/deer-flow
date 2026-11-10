"""SQLite persistence for owner-scoped diagnoses, leases, and analysis attempts."""

import json
import sqlite3
import time
from contextlib import closing
from uuid import uuid4

from fastapi import HTTPException

from api.settings import SESSION_TTL, database_path, get_credentials_cipher

TERMINAL = {"completed", "failed", "run_pending"}


def _connect():
    path = database_path()
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
    return db


def read_model(owner: str):
    with closing(_connect()) as db:
        return db.execute(
            "SELECT provider,model,encrypted_key FROM ci_model_settings WHERE owner=? AND expires_at>?",
            (owner, time.time()),
        ).fetchone()


def save_model(owner: str, provider: str, model: str, key: str) -> None:
    encrypted_key = get_credentials_cipher().encrypt_text(key)
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM ci_model_settings WHERE expires_at<=?", (time.time(),))
        db.execute(
            """INSERT INTO ci_model_settings VALUES (?,?,?,?,?)
            ON CONFLICT(owner) DO UPDATE SET provider=excluded.provider,model=excluded.model,
            encrypted_key=excluded.encrypted_key,expires_at=excluded.expires_at""",
            (owner, provider, model, encrypted_key, int(time.time()) + SESSION_TTL),
        )


def delete_model(owner: str) -> None:
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM ci_model_settings WHERE owner=?", (owner,))


def create(owner, reference, *, deep=False):
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


def read(task_id, owner):
    with closing(_connect()) as db:
        row = db.execute("SELECT payload,lease_until FROM ci_diagnoses WHERE id=? AND owner=?", (task_id, owner)).fetchone()
    if row is None:
        raise HTTPException(404, "Diagnosis not found")
    task = json.loads(row["payload"])
    if task.get("github_binding") or task.get("expires_at", 0) <= time.time():
        raise HTTPException(404, "Diagnosis not found")
    return task, row["lease_until"]


def claim(task_id, owner):
    lease = uuid4().hex
    with closing(_connect()) as db, db:
        changed = db.execute(
            """UPDATE ci_diagnoses SET status='running',lease=?,lease_until=?
            WHERE id=? AND owner=? AND json_extract(payload, '$.github_binding') IS NULL
              AND json_extract(payload, '$.expires_at')>? AND (status='queued' OR (status='running' AND lease_until<?))""",
            (lease, time.time() + 155, task_id, owner, time.time(), time.time()),
        ).rowcount
    return lease if changed else None


def record(task_id, owner, lease, kind, **data):
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
        task["status"] = status
        task["events"].append({"seq": len(task["events"]) + 1, "kind": kind, "recorded_at": time.time(), **data})
        db.execute("UPDATE ci_diagnoses SET payload=?,status=? WHERE id=?", (json.dumps(task), status, task_id))
        return True


def analysis_event(task_id, owner, event, *, claim=False, attempt=1, retry_after=None):
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
