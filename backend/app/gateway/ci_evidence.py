"""Read evidence for an exact GitHub Actions run attempt."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from app.gateway.ci_advice import repair_advice

API_ROOT = "https://api.github.com"
MAX_LOG_BYTES = 4 * 1024 * 1024
RUN_PATH = re.compile(r"/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/actions/runs/([1-9][0-9]{0,19})(?:/attempts/([1-9][0-9]{0,8}))?/?")
CHECKOUT_SHA = re.compile(r"(?:^|\n)(?:\S+Z )?([0-9a-f]{40})\r?(?=\n|$)")


@dataclass(frozen=True)
class RunReference:
    owner: str
    repository: str
    run_id: int
    attempt: int | None

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repository}/actions/runs/{self.run_id}"


def parse_run_url(url: str) -> RunReference:
    parts = urlsplit(url.strip())
    match = RUN_PATH.fullmatch(parts.path)
    if parts.scheme != "https" or parts.netloc.lower() != "github.com" or not match:
        raise ValueError("Expected a GitHub Actions run or attempt URL")
    owner, repository, run_id, attempt = match.groups()
    if repository in {".", ".."}:
        raise ValueError("Invalid repository name")
    return RunReference(owner, repository, int(run_id), int(attempt) if attempt else None)


@dataclass(frozen=True)
class JobEvidence:
    job: dict
    log: str | None
    log_state: str
    checkout_shas: tuple[str, ...]
    annotations: tuple[dict, ...] = ()


@dataclass(frozen=True)
class AttemptEvidence:
    run: dict
    attempt: int
    failed_jobs: tuple[JobEvidence, ...]


class PrivateRunError(ValueError):
    """The run cannot be processed through the public-repository flow."""


def checkout_shas(log: str) -> tuple[str, ...]:
    """Only accept full hashes printed by a checkout's git log command."""
    shas = []
    waiting_for_sha = False
    for line in log.split("\n"):
        if "[command]" in line:
            waiting_for_sha = bool(re.search(r"\bgit(?:\.exe)?\s+log\s+-1\s+--format=%H(?:\s|$)", line))
        elif waiting_for_sha and (match := CHECKOUT_SHA.fullmatch(line)):
            shas.append(match[1])
            waiting_for_sha = False
        elif waiting_for_sha and "##[" in line:
            waiting_for_sha = False
    return tuple(dict.fromkeys(shas))


class GitHubCIEvidence:
    def __init__(self, client: httpx.AsyncClient, token: str | None = None):
        self.client = client
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str) -> dict:
        response = await self.client.get(API_ROOT + path, headers=self.headers, follow_redirects=False)
        response.raise_for_status()
        return response.json()

    async def collect(
        self,
        reference: RunReference,
        *,
        public_only: bool = False,
        on_attempt: Callable[[dict, int], Awaitable[None]] | None = None,
        on_job: Callable[[JobEvidence], Awaitable[None]] | None = None,
    ) -> AttemptEvidence:
        if reference.attempt is None:
            current = await self._get(reference.api_path)
            attempt = int(current["run_attempt"])
        else:
            attempt = reference.attempt
        path = f"{reference.api_path}/attempts/{attempt}"
        run = await self._get(path)
        if public_only and (run.get("repository", {}).get("private") is not False):
            raise PrivateRunError
        if on_attempt:
            await on_attempt(run, attempt)
        jobs = []
        for page in range(1, 101):
            batch = (await self._get(f"{path}/jobs?per_page=100&page={page}"))["jobs"]
            jobs.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("GitHub Actions job pagination limit reached")
        failures = []
        for job in jobs:
            if job["conclusion"] != "failure":
                continue
            job_path = f"/repos/{reference.owner}/{reference.repository}/actions/jobs/{int(job['id'])}/logs"
            log, state = await self._read_log(job_path)
            annotations = await self._read_annotations(reference, job) if log is None else ()
            evidence = JobEvidence(job, log, state, checkout_shas(log) if log else (), annotations)
            failures.append(evidence)
            if on_job:
                await on_job(evidence)
        return AttemptEvidence(run, attempt, tuple(failures))

    async def _read_annotations(self, reference: RunReference, job: dict) -> tuple[dict, ...]:
        check_url = job.get("check_run_url")
        if not isinstance(check_url, str):
            return ()
        prefix = f"{API_ROOT}/repos/{reference.owner}/{reference.repository}/check-runs/"
        match = re.fullmatch(re.escape(prefix) + r"([1-9][0-9]*)", check_url, re.IGNORECASE)
        if not match:
            return ()
        try:
            response = await self.client.get(
                f"{prefix}{match[1]}/annotations",
                params={"per_page": 30},
                headers=self.headers,
                follow_redirects=False,
            )
        except httpx.RequestError:
            return ()
        if response.status_code != 200:
            return ()
        return tuple(response.json())

    async def read_source(self, reference: RunReference, sha: str, path: str, *, max_bytes: int = MAX_LOG_BYTES) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or any(segment in {"", ".", ".."} for segment in path.split("/")):
            raise ValueError("Expected an exact commit SHA and repository-relative path")
        async with self.client.stream(
            "GET",
            f"{API_ROOT}/repos/{reference.owner}/{reference.repository}/contents/{quote(path)}",
            params={"ref": sha},
            headers={**self.headers, "Accept": "application/vnd.github.raw+json"},
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("Source exceeds the byte limit")
                data.extend(chunk)
            return bytes(data)

    async def _read_log(self, path: str) -> tuple[str | None, str]:
        async with self.client.stream("GET", API_ROOT + path, headers=self.headers, follow_redirects=False) as response:
            if response.status_code == 200:
                return await self._decode_log(response)
            if response.status_code in {403, 404, 410}:
                state = "rate_limited" if response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers else "unavailable"
                return None, state
            if response.status_code != 302:
                response.raise_for_status()
            location = response.headers.get("Location", "")
        parts = urlsplit(location)
        host = parts.hostname or ""
        if parts.scheme != "https" or parts.username is not None or parts.port not in {None, 443} or not (host.endswith(".actions.githubusercontent.com") or re.fullmatch(r"productionresultssa[0-9]+\.blob\.core\.windows\.net", host)):
            return None, "unsupported_redirect"
        request = httpx.Request("GET", location)
        response = await self.client.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            if response.status_code in {403, 404, 410}:
                return None, "unavailable"
            response.raise_for_status()
            return await self._decode_log(response)
        finally:
            await response.aclose()

    @staticmethod
    async def _decode_log(response: httpx.Response) -> tuple[str | None, str]:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > MAX_LOG_BYTES:
                return None, "too_large"
            data.extend(chunk)
        return data.decode("utf-8-sig", errors="replace"), "available"


def annotation_evidence(annotations, job_id):
    """Keep bounded public check annotations with conservative credential redaction."""
    result = []
    for index, annotation in enumerate(annotations[:10], 1):
        if not isinstance(annotation, dict):
            continue
        path = annotation.get("path")
        line = annotation.get("start_line")
        message = annotation.get("message")
        if not isinstance(path, str) or type(line) is not int or not isinstance(message, str):
            continue
        message = re.sub(r"https?://[^\s]+", "[url]", message)
        message = re.sub(r"(?i)\b(?:bearer|token|password|secret|api[_ -]?key)\b[^\n]*", "[redacted]", message)
        message = re.sub(r"\b[A-Za-z0-9_+/=-]{24,}\b", "[redacted]", message)
        result.append(
            {
                "id": f"job-{job_id}-annotation-{index}",
                "path": " ".join(path.split())[:240],
                "line": line,
                "message": " ".join(message.split())[:500],
            }
        )
    return result


_LOG_MARKER = re.compile(
    r"curl: \(22\) The requested URL returned error: ([45][0-9]{2})"
    r"|ERR_PNPM_OUTDATED_LOCKFILE|\bTS[0-9]{4}\b|\bMODULE_NOT_FOUND\b"
)


def job_event_data(evidence: JobEvidence, *, private: bool) -> dict:
    job = evidence.job
    job_id = int(job["id"])
    markers = list(_LOG_MARKER.finditer(evidence.log or ""))
    return {
        "job_id": job_id,
        "name": job.get("name", "")[:200],
        "steps": [step["name"][:200] for step in job.get("steps", []) if step.get("conclusion") == "failure"][:20],
        "log_state": evidence.log_state,
        "checkout_shas": evidence.checkout_shas[:10],
        "annotation_count": len(evidence.annotations),
        "annotations": [] if private else annotation_evidence(evidence.annotations, job_id),
        "advice": repair_advice(evidence.log),
        "log_excerpt": [] if private else list(dict.fromkeys(match[0] for match in markers))[:12],
        "http_errors": sorted({match[1] for match in markers if match[1]}),
    }
