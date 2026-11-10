"""Client for the operator-configured CI Trace verification runner."""

import os
from pathlib import Path

import httpx


def runner_check(url, source, mode, expected):
    token = os.getenv("CI_TRACE_RUNNER_TOKEN", "").strip()
    if not token:
        token = Path(os.environ.get("CI_TRACE_RUNNER_TOKEN_FILE", "")).read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}", "X-CI-Check": mode}
    if mode == "node_lookup":
        headers["X-CI-Module"] = expected
    with httpx.Client(timeout=13, follow_redirects=False, trust_env=False) as client:
        response = client.post(url.rstrip("/") + "/check", headers=headers, content=source)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or not {"code", "symptom"} <= result.keys():
        raise ValueError("Invalid runner response")
    code, symptom = result["code"], result["symptom"]
    if type(code) is not int or (symptom is not None and type(symptom) is not (str if mode == "node_lookup" else int)):
        raise ValueError("Invalid runner response")
    return code, symptom
