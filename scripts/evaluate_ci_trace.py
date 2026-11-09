"""Evaluate a CI Trace diagnosis export against a reference case."""

import argparse
import json
from pathlib import Path


def evaluate(trace, expected):
    events = trace["events"]
    jobs = [event for event in events if event["kind"] == "job"]
    checks = {
        "schema": trace.get("schema_version") == 1 and trace.get("scope") == "diagnosis",
        "event_order": [event["seq"] for event in events] == list(range(1, len(events) + 1)),
        "terminal_status": bool(events) and events[-1]["kind"] == expected.get("status", "completed") == trace["status"],
    }
    for field in ("url", "repository", "attempt"):
        if field in expected:
            checks[field] = trace.get(field) == expected[field]
    if "failed_job_count" in expected:
        checks["failed_job_count"] = len(jobs) == expected["failed_job_count"]
    for index, job in enumerate(expected.get("jobs", []), start=1):
        checks[f"job_{index}"] = any(all(actual.get(key) == value for key, value in job.items() if key != "steps") and set(job.get("steps", [])) <= set(actual.get("steps", [])) for actual in jobs)
    if "verification" in expected:
        checks["verification"] = any(event["kind"] == "verification" and all(event.get(key) == value for key, value in expected["verification"].items()) for event in events)
    duration = None
    if checks["terminal_status"] and all(isinstance(event.get("recorded_at"), (int, float)) for event in (events[0], events[-1])):
        duration = round(max(0, events[-1]["recorded_at"] - events[0]["recorded_at"]), 3)
    return {"passed": all(checks.values()), "checks": checks, "elapsed_seconds": duration}


def evaluate_model(trace, model, expected):
    events = model["events"]
    attempt = events[-1].get("analysis_attempt", 1)
    events = [event for event in events if event.get("analysis_attempt", 1) == attempt]
    terminal = events[-1]
    findings = terminal.get("findings", [])
    analyzed = set(terminal.get("coverage", {}).get("analyzed_job_ids", []))
    allowed = {ref for job in trace["events"] if job["kind"] == "job" and job["job_id"] in analyzed for ref in [f"job-{job['job_id']}", *(item["id"] for item in job.get("annotations", []))]}
    checks = {
        "same_diagnosis": model.get("schema_version") == 1 and model.get("scope") == "model_analysis" and bool(trace.get("id")) and model.get("diagnosis_id") == trace.get("id"),
        "completed": events[0]["kind"] == "started" and terminal["kind"] == "result",
        "findings_present": bool(findings),
        "references_valid": all(set(item["evidence_ids"]) <= allowed and (item["kind"] not in {"observation", "inference"} or bool(item["evidence_ids"])) for item in findings),
    }
    for kind in expected.get("required_kinds", []):
        checks[f"has_{kind}"] = any(item["kind"] == kind for item in findings)
    if "analyzed_job_ids" in expected:
        checks["expected_jobs_analyzed"] = set(expected["analyzed_job_ids"]) <= analyzed
    for index, term in enumerate(expected.get("required_terms", []), start=1):
        checks[f"reference_term_{index}"] = any(term.casefold() in item["text"].casefold() for item in findings)
    start, end = events[0].get("recorded_at"), terminal.get("recorded_at")
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "elapsed_seconds": round(max(0, end - start), 3) if start is not None and end is not None else None,
        "tool_calls": sum(bool(event.get("item", {}).get("tool")) for event in events),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("expected", type=Path)
    parser.add_argument("--model-trace", type=Path)
    args = parser.parse_args()
    try:
        trace = json.loads(args.trace.read_text(encoding="utf-8-sig"))
        expected = json.loads(args.expected.read_text(encoding="utf-8-sig"))
        result = evaluate(trace, expected)
        if args.model_trace:
            model = json.loads(args.model_trace.read_text(encoding="utf-8-sig"))
            model_result = evaluate_model(trace, model, expected.get("model", {}))
            result["model"] = model_result
            result["passed"] = result["passed"] and model_result["passed"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
