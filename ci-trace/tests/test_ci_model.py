import asyncio

import httpx
import pytest
from fastapi import HTTPException

from api import ci_model


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(302, headers={"Location": "https://foreign.test/steal"}),
        httpx.Response(200, content=b"x" * (ci_model.MAX_RESPONSE_BYTES + 1)),
    ],
)
def test_provider_redirects_and_oversized_bodies_are_rejected(monkeypatch, reply):
    requests = []

    def handle(request):
        requests.append(request.url.host)
        return reply

    client = httpx.AsyncClient
    monkeypatch.setattr(ci_model.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(HTTPException) as error:
        asyncio.run(ci_model.model_message("deepseek", "test-model", "secret", []))
    assert error.value.status_code == 502
    assert requests == ["api.deepseek.com"]
