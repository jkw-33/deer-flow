"""Public CI Trace projections and downloadable artifacts."""

import json


def public_event(event: dict) -> dict:
    return {key: value for key, value in event.items() if key != "patch"}


def public_task(task: dict) -> dict:
    return {
        **{key: value for key, value in task.items() if key not in {"github_binding", "events", "analysis"}},
        "private": bool(task.get("github_binding")),
        "analysis_started": bool(task.get("analysis")),
        "events": [public_event(event) for event in task["events"]],
    }


def render_artifact(task: dict, artifact: str) -> str | None:
    if artifact == "model-trace.json":
        if not task.get("analysis"):
            return None
        trace = {"schema_version": 1, "scope": "model_analysis", "diagnosis_id": task["id"], "events": task["analysis"]}
        return json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
    if artifact == "trace.json":
        trace = {"schema_version": 1, "scope": "diagnosis", **public_task(task)}
        return json.dumps(trace, ensure_ascii=False, indent=2) + "\n"

    verification = next((event for event in reversed(task["events"]) if event["kind"] == "verification"), None)
    if artifact == "report.txt" and task["status"] == "completed":
        return _report(task, verification)
    if artifact == "repair.diff" and verification:
        return verification.get("patch")
    if artifact == "verification.log" and verification:
        return _verification_log(verification)
    return None


def _report(task: dict, event: dict | None) -> str:
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
    return "\n".join(lines) + "\n"


def _verification_log(event: dict) -> str:
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
