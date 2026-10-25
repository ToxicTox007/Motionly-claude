"""A protocol-level fake for the OpenAI-compatible and Azure HTTP adapters.

``app.services.model_adapters.OpenAICompatibleAdapter`` and ``AzureOpenAIAdapter``
build an ``httpx.Client(..., trust_env=False)`` per call and send one POST. They
do not accept an injected transport, so existing tests fake them by
monkeypatching ``httpx.Client.request``. This helper provides the same shape
through either path, with no sockets:

- ``as_httpx_handler`` returns a ``request -> httpx.Response`` callable that can
  back an ``httpx.MockTransport`` (used where a transport can be injected).
- ``install(monkeypatch)`` patches ``httpx.Client.request`` so the adapters'
  own clients route through this server instead of the network.

Scenarios are programmable per endpoint: a status code, a canned JSON body,
malformed (non-JSON) bodies, or a raised timeout. Every request is recorded.

The server is scenario-driven and never resolves DNS or opens a socket, so it
coexists with the no-network guard in ``conftest.py``.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import httpx


class ServerOutcome(StrEnum):
    SUCCESS = "success"
    MALFORMED_JSON = "malformed_json"
    HTTP_400 = "http_400"
    HTTP_401 = "http_401"
    HTTP_402 = "http_402"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"


_STATUS_BY_OUTCOME = {
    ServerOutcome.HTTP_400: 400,
    ServerOutcome.HTTP_401: 401,
    ServerOutcome.HTTP_402: 402,
    ServerOutcome.HTTP_403: 403,
    ServerOutcome.HTTP_429: 429,
    ServerOutcome.HTTP_5XX: 503,
}

# Logical endpoints the fake understands. ``path`` is matched by suffix so both
# OpenAI-compatible (``/v1/chat/completions``) and Azure deployment-scoped
# (``/openai/deployments/<dep>/chat/completions``) URLs resolve to one handler.
CHAT_COMPLETIONS = "chat/completions"
AUDIO_TRANSCRIPTIONS = "audio/transcriptions"
EMBEDDINGS = "embeddings"
IMAGES_GENERATIONS = "images/generations"
ASYNC_SUBMIT = "jobs"
ASYNC_STATUS = "jobs/status"

_ENDPOINTS = (
    AUDIO_TRANSCRIPTIONS,
    IMAGES_GENERATIONS,
    CHAT_COMPLETIONS,
    EMBEDDINGS,
    ASYNC_STATUS,
    ASYNC_SUBMIT,
)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    endpoint: str
    headers: Mapping[str, str]
    json_body: object | None
    has_multipart: bool


@dataclass
class _Scenario:
    outcome: ServerOutcome = ServerOutcome.SUCCESS
    body: Mapping[str, object] | None = None


def _default_body(endpoint: str) -> Mapping[str, object]:
    if endpoint == CHAT_COMPLETIONS:
        return {
            "choices": [{"message": {"content": "fake"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
    if endpoint == AUDIO_TRANSCRIPTIONS:
        return {
            "text": "hello world",
            "language": "en",
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.48},
                {"word": "world", "start": 0.5, "end": 1.0},
            ],
        }
    if endpoint == EMBEDDINGS:
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]}
    if endpoint == IMAGES_GENERATIONS:
        return {"id": "fake", "data": [{"url": "https://fake.invalid/image.png"}]}
    if endpoint == ASYNC_SUBMIT:
        return {"id": "fake-job-1", "status": "running"}
    if endpoint == ASYNC_STATUS:
        return {"id": "fake-job-1", "status": "succeeded"}
    return {}


class FakeProviderServer:
    """Scenario-programmable, in-process fake for the HTTP inference adapters."""

    def __init__(self) -> None:
        self._scenarios: dict[str, _Scenario] = {}
        self.requests: list[RecordedRequest] = []

    def program(
        self,
        endpoint: str,
        *,
        outcome: ServerOutcome = ServerOutcome.SUCCESS,
        body: Mapping[str, object] | None = None,
    ) -> None:
        """Set the outcome and (optional) response body for one endpoint."""
        if endpoint not in _ENDPOINTS:
            raise ValueError(f"unknown fake endpoint: {endpoint}")
        self._scenarios[endpoint] = _Scenario(outcome=outcome, body=body)

    def _endpoint_for(self, url: str) -> str:
        path = httpx.URL(url).path
        for endpoint in _ENDPOINTS:
            if path.endswith("/" + endpoint) or path.endswith(endpoint):
                return endpoint
        raise ValueError(f"fake provider server has no handler for {url}")

    def _record(self, request: httpx.Request, endpoint: str) -> None:
        json_body: object | None = None
        has_multipart = "multipart/form-data" in request.headers.get("content-type", "")
        if not has_multipart and request.content:
            try:
                json_body = request.read() and _json.loads(request.content)
            except ValueError:
                json_body = None
        self.requests.append(
            RecordedRequest(
                method=request.method,
                url=str(request.url),
                endpoint=endpoint,
                headers=dict(request.headers),
                json_body=json_body,
                has_multipart=has_multipart,
            )
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Resolve one request to a response, honouring the programmed scenario."""
        endpoint = self._endpoint_for(str(request.url))
        self._record(request, endpoint)
        scenario = self._scenarios.get(endpoint, _Scenario())
        if scenario.outcome == ServerOutcome.TIMEOUT:
            raise httpx.TimeoutException("fake timeout", request=request)
        status = _STATUS_BY_OUTCOME.get(scenario.outcome)
        if status is not None:
            return httpx.Response(status, json={"error": {"status": status}}, request=request)
        if scenario.outcome == ServerOutcome.MALFORMED_JSON:
            return httpx.Response(
                200, content=b"not-json<broken", request=request
            )
        body = scenario.body if scenario.body is not None else _default_body(endpoint)
        headers = {"x-request-id": "fake-provider-request"}
        return httpx.Response(200, json=dict(body), headers=headers, request=request)

    def as_httpx_handler(self) -> Callable[[httpx.Request], httpx.Response]:
        """A handler for ``httpx.MockTransport(server.as_httpx_handler())``."""
        return self.handle

    def install(self, monkeypatch) -> None:  # noqa: ANN001 - pytest MonkeyPatch
        """Patch ``httpx.Client.request`` so adapter-owned clients use this server.

        The OpenAI-compatible and Azure adapters build their own clients, so this
        routes those calls through the server without changing adapter code.
        """
        server = self

        def _request(client, method, url, **kwargs):  # noqa: ANN001
            del client
            request = httpx.Request(
                method,
                url,
                headers=kwargs.get("headers"),
                json=kwargs.get("json"),
                data=kwargs.get("data"),
                files=kwargs.get("files"),
            )
            return server.handle(request)

        monkeypatch.setattr(httpx.Client, "request", _request)
