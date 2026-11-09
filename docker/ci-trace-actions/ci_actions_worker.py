"""Execute one bounded check using public source bytes at an exact commit."""

import hashlib
import json
import os
import re
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, build_opener

from ci_verification import MAX_SCRIPT_BYTES, bash_check, node_check, verify

IMAGE = "node:22-bookworm-slim"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    repo, sha, path, mode, expected, digest = (os.environ[key] for key in ("SOURCE_REPOSITORY", "SOURCE_SHA", "SOURCE_PATH", "CHECK_MODE", "EXPECTED", "SOURCE_SHA256"))
    if (
        not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo)
        or repo.split("/")[1] in {".", ".."}
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or len(path) > 240
        or not re.fullmatch(r"[A-Za-z0-9_./-]+\.sh", path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Invalid public source coordinates")
    url = f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(path)}"
    with build_opener(NoRedirect()).open(url, timeout=10) as response:
        source = response.read(MAX_SCRIPT_BYTES + 1)
    if len(source) > MAX_SCRIPT_BYTES or hashlib.sha256(source).hexdigest() != digest:
        raise ValueError("Source does not match Gateway digest")
    if mode == "bash_syntax" and re.fullmatch(r"[1-9][0-9]{0,5}", expected):
        result = verify(source, path, int(expected), lambda data: bash_check(IMAGE, data))
    elif mode == "node_lookup" and re.fullmatch(r"[A-Za-z0-9_-]+\.mjs", expected):
        result = verify(source, path, expected, lambda data: node_check(IMAGE, data, expected), mode=mode)
    else:
        raise ValueError("Unsupported check")
    result.pop("patch", None)
    print("CI_TRACE_RESULT=" + json.dumps({"source_sha256": digest, **result}, separators=(",", ":")))


if __name__ == "__main__":
    main()
