"""HTTP client for the SMAC API — the bridge's only door into the server.

The bridge is a pure API client: same front door as every other client,
so the server's auth, workspace wall, and rate limit all apply unchanged.
"""

import json
from typing import Any

import httpx


class SmacApiError(Exception):
    """A tool-reportable failure: carries the message the LLM should read."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SmacApi:
    """Thin async httpx wrapper: auth header, error mapping, workspace cache."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self._workspace_id: str | None = None

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "headers": {"X-API-Key": self._api_key},
            "timeout": 10.0,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        """Issue an HTTP request; return the parsed JSON body on 2xx.

        Raises SmacApiError, carrying a message meant to be read by the LLM,
        on connection failure, an invalid/rejected API key, or any other
        non-2xx response (using the server's own envelope message).
        """
        try:
            async with self._client() as client:
                response = await client.request(
                    method, path, json=json_body, params=params
                )
        except httpx.HTTPError:
            raise SmacApiError(
                f"SMAC server is not reachable at {self._base_url} — is it running?"
            )
        if response.status_code == 401:
            raise SmacApiError(
                "SMAC rejected the API key — was the agent deleted, "
                "or the key mistyped?"
            )
        if response.is_success:
            return response.json()
        raise SmacApiError(_envelope_message(response))

    async def me(self) -> dict:
        """GET /members/me — the caller's own profile."""
        result = await self.request("GET", "/members/me")
        assert isinstance(result, dict)
        return result

    async def workspace_id(self) -> str:
        """The caller's workspace_id, resolved lazily via `me()` and cached."""
        if self._workspace_id is None:
            self._workspace_id = str((await self.me())["workspace_id"])
        return self._workspace_id


def _envelope_message(response: httpx.Response) -> str:
    """The server's own words, whatever the failure."""
    try:
        body = response.json()
        if "error" in body:
            return str(body["error"]["message"])
        # SMAC itself always wraps errors (including 422s) in the "error"
        # envelope above; this fallback is defensive for non-SMAC JSON
        # bodies -- e.g. a proxy sitting in front of the server -- that
        # return a differently-shaped payload.
        return json.dumps(body)
    except (json.JSONDecodeError, KeyError, TypeError):
        return f"SMAC returned HTTP {response.status_code}"
