"""Bounded Bash syntax verification for public CI evidence."""

import difflib
import re
import subprocess
from uuid import uuid4

DIAGNOSTIC = re.compile(r"line ([1-9][0-9]{0,5}): syntax error: unexpected end of file")
SCRIPT_ERROR = re.compile(r"(?:^|\s)\./([\w./-]+\.sh): " + DIAGNOSTIC.pattern + r"\r?$", re.MULTILINE | re.ASCII)
NODE_SCRIPT = re.compile(rb"(?:#![^\r\n]{1,120}\r?\n)?node ((?:\./|/probe/)[a-zA-Z0-9_-]+\.mjs)(?:\r?\n)?")
NODE_ERROR = re.compile(
    r"""Error: Cannot find module ['"](/[^'"\r\n]+/[A-Za-z0-9_-]+\.mjs)"""
    r"""(?:\r|\\r|\r?\n[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z )['"]"""
)
# The wrapper provides only an empty module so the test measures lookup.
NODE_PROBE = (
    'const fs = require("node:fs");'
    'const run = require("node:child_process").spawnSync;'
    'fs.writeFileSync("/probe/" + process.argv[1], "");'
    'const result = run("/bin/bash", ["--noprofile", "--norc", "-e", "-s"],'
    '{ cwd: "/probe", stdio: "inherit" });'
    "process.exit(result.status ?? 125);"
)
MAX_SCRIPT_BYTES = 16 * 1024


def candidate(log, shas):
    matches = set(SCRIPT_ERROR.findall(log or ""))
    if len(shas) != 1 or len(matches) != 1:
        return None
    path, line = matches.pop()
    if len(path) > 240 or any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return shas[0], path, int(line)


def node_target(log, shas):
    if len(shas) != 1 or "MODULE_NOT_FOUND" not in (log or ""):
        return None
    matches = set()
    groups = re.split(r"(?m)^(?:\S+Z )?##\[group\]Run ", log)
    for group in groups[1:]:
        command = group.split("\n", 1)[0].rstrip("\r")
        path = re.fullmatch(r"bash \./([A-Za-z0-9_/-]+\.sh)", command)
        if not path:
            continue
        for match in NODE_ERROR.finditer(group):
            if "MODULE_NOT_FOUND" in group[match.end() : match.end() + 1500]:
                matches.add((path[1], match[1].rsplit("/", 1)[-1]))
    if len(matches) != 1:
        return None
    path, module = matches.pop()
    return shas[0], path, module


def _container_check(image, source, entrypoint, args, options=()):
    name = f"ci-trace-check-{uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--log-driver=none",
        "--read-only",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=64m",
        "--cpus=0.5",
        "--pids-limit=16",
        "-i",
        *options,
        f"--entrypoint={entrypoint}",
        "--",
        image,
        *args,
    ]
    try:
        result = subprocess.run(command, input=source, capture_output=True, timeout=8, check=False)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=5, check=False)
        raise
    if result.returncode in {125, 126, 127}:
        raise OSError("Verification container could not run")
    return result.returncode, result.stderr.decode("utf-8", errors="replace")


def bash_check(image, source):
    code, stderr = _container_check(image, source, "/bin/bash", ["--noprofile", "--norc", "-n", "-s"])
    match = DIAGNOSTIC.search(stderr)
    return code, int(match[1]) if match else None


def node_check(image, source, module):
    script = NODE_SCRIPT.fullmatch(source)
    if not script or script[1].decode().rsplit("/", 1)[-1] != module:
        raise ValueError("Unsupported Node lookup script")
    code, stderr = _container_check(
        image,
        source,
        "/usr/local/bin/node",
        ["-e", NODE_PROBE, module],
        ["--tmpfs=/probe:rw,noexec,nosuid,nodev,size=64k,mode=1777"],
    )
    match = NODE_ERROR.search(stderr)
    return code, module if match and match[1] == f"/probe/{module}" and "MODULE_NOT_FOUND" in stderr else None


def lf_lines(text):
    *complete, tail = text.split("\n")
    return [line + "\n" for line in complete] + ([tail] if tail else [])


def verify(source, path, expected, check, *, mode="bash_syntax"):
    if len(source) > MAX_SCRIPT_BYTES or b"\0" in source or b"\r\n" not in source:
        return {"status": "unconfirmed"}
    try:
        original = source.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "unconfirmed"}
    normalized = original.replace("\r\n", "\n")
    baseline, symptom = check(source)
    result = {"status": "unconfirmed", "baseline": baseline}
    if mode == "bash_syntax":
        result["error_line"] = symptom
    elif symptom == expected:
        result["missing_module"] = symptom
    if (baseline, symptom) != (2 if mode == "bash_syntax" else 1, expected):
        return result
    rerun, _ = check(normalized.encode("utf-8"))
    result["rerun"] = rerun
    if rerun != 0:
        return result
    diff = difflib.unified_diff(lf_lines(original), lf_lines(normalized), f"a/{path}", f"b/{path}")
    result["patch"] = "".join(part if part.endswith("\n") else part + "\n\\ No newline at end of file\n" for part in diff)
    result["status"] = "verified"
    return result
