import asyncio
import json

import pytest

from api.ci_investigation import investigate


def answer(text, ref):
    return {"content": json.dumps({"findings": [{"kind": "observation", "text": text, "evidence_ids": [ref]}]})}


def test_multi_job_investigation_uses_scoped_evidence_and_synthesizes():
    visited = []

    async def ask(messages, tool=None):
        if tool:
            return {"tool_calls": [{"id": "call", "function": {"name": "read_job_evidence"}}]}
        if messages[-1]["role"] == "tool":
            evidence = json.loads(messages[-1]["content"])
            visited.append(evidence["id"])
            assert str(evidence["job_id"]) in messages[1]["content"]
            assert "patch" not in messages[-1]["content"]
            return answer(evidence["id"], evidence["id"])
        summary = json.loads(messages[-1]["content"])
        assert len(summary["investigations"]) == 3
        assert summary["total_jobs"] == 4
        return answer("summary", "job-1")

    jobs = [{"job_id": number} for number in range(1, 5)]
    verification = {"source_job": 1, "patch": "never sent"}
    result = asyncio.run(investigate(jobs, [verification], ask))
    assert visited == ["job-1", "job-2", "job-3"]
    assert result["text"] == "summary"
    assert result["analyzed_jobs"] == 3


def test_log_excerpt_is_scoped_to_the_assigned_job():
    async def run(evidence_id):
        trace = []

        async def notify(event):
            trace.append(event)

        async def ask(messages, tool=None):
            if tool:
                name = tool["function"]["name"]
                arguments = {"evidence_id": evidence_id} if name == "read_log_excerpt" else {}
                return {"tool_calls": [{"id": name, "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            assert json.loads(messages[-1]["content"]) == {"id": "job-1", "excerpt": ["MODULE_NOT_FOUND"]}
            return answer("job-1 MODULE_NOT_FOUND", "job-1")

        result = await investigate([{"job_id": 1, "log_excerpt": ["MODULE_NOT_FOUND"]}], [], ask, notify)
        assert result["text"] == "job-1 MODULE_NOT_FOUND"
        assert [event.get("tool") for event in trace if event.get("tool")] == ["read_job_evidence", "read_log_excerpt"]

    asyncio.run(run("job-1"))
    with pytest.raises(ValueError, match="No delegated investigations completed"):
        asyncio.run(run("job-2"))


def test_rejects_citations_outside_job_evidence():
    async def ask(messages, tool=None):
        if tool:
            return {"tool_calls": [{"id": "call", "function": {"name": "read_job_evidence"}}]}
        return answer("foreign citation", "job-2")

    with pytest.raises(ValueError, match="No delegated investigations completed"):
        asyncio.run(investigate([{"job_id": 1}], [], ask))
