"""Loopback HTTP client from the STDIO Bridge to the Local Engine.

Thin by contract (ADR 0001): JSON in, JSON out, frozen per-tool timeout
budgets (``docs/MCP_TOOLS.md`` §0.3), token header when configured. Transport
failures propagate as ``httpx.RequestError`` — translation to the frozen error
envelope lives in ``mcp.errors`` and is applied by the tool layer.
"""

from collections.abc import Mapping
from typing import Any

import httpx

from evoblue_video_mcp.config import Settings

#: Frozen §0.3 budgets — Bridge-to-Engine budgets stay well under the ~60 s
#: MCP client tool timeout.
TIMEOUT_SUBMIT_S = 10.0
TIMEOUT_STATUS_S = 5.0
TIMEOUT_REPORT_S = 10.0
TIMEOUT_LIST_S = 10.0
TIMEOUT_SEARCH_S = 10.0
TIMEOUT_CANCEL_S = 10.0
TIMEOUT_DIAGNOSE_LOCAL_S = 15.0
TIMEOUT_DIAGNOSE_NETWORK_S = 30.0
TIMEOUT_HEALTH_S = 3.0


class EngineClient:
    """One Bridge process talks to one loopback Engine through this client."""

    def __init__(self, http: httpx.AsyncClient, *, base_url: str, token: str | None) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {"X-Local-Token": token} if token else {}

    @classmethod
    def from_settings(
        cls, settings: Settings, *, token: str | None = None
    ) -> "EngineClient":
        """Build a client for the loopback Engine described by ``settings``.

        ``token`` overrides the settings value (the stdio entry point resolves
        the persisted data-directory token before constructing the client).
        """
        base_url = f"http://{settings.engine_host}:{settings.engine_port}"
        resolved = token if token is not None else (settings.local_access_token or None)
        # trust_env=False: loopback traffic must never honor HTTP(S)_PROXY —
        # a proxy would answer a dead Engine with 502 and mask ENGINE_NOT_READY.
        return cls(
            httpx.AsyncClient(trust_env=False), base_url=base_url, token=resolved
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def health(self) -> dict[str, Any] | None:
        """Probe ``/api/health``; ``None`` means the Engine is unreachable.

        Timeouts and other transport errors propagate so the tool layer can
        answer ``ENGINE_TIMEOUT`` instead of claiming the Engine is down.
        """
        try:
            status, body = await self.get_json("/api/health", timeout_s=TIMEOUT_HEALTH_S)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return None
        if not 200 <= status < 300 or not isinstance(body, dict):
            return None
        return body

    async def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        timeout_s: float,
    ) -> tuple[int, object | None]:
        """GET one Engine path; return ``(status, parsed JSON or None)``."""
        response = await self._http.get(
            self._base_url + path,
            params=_query_params(params),
            headers=self._headers,
            timeout=timeout_s,
        )
        return response.status_code, _parse_json(response)

    async def post_json(
        self,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        timeout_s: float,
    ) -> tuple[int, object | None]:
        """POST one Engine path; return ``(status, parsed JSON or None)``."""
        response = await self._http.post(
            self._base_url + path,
            json=dict(payload) if payload is not None else None,
            headers=self._headers,
            timeout=timeout_s,
        )
        return response.status_code, _parse_json(response)

    async def aclose(self) -> None:
        await self._http.aclose()


def _query_params(params: Mapping[str, object] | None) -> dict[str, str] | None:
    """Drop ``None`` entries and stringify, so optional filters stay optional."""
    if params is None:
        return None
    return {
        key: str(value) for key, value in params.items() if value is not None
    }


def _parse_json(response: httpx.Response) -> object | None:
    try:
        parsed: object = response.json()
    except ValueError:
        # Non-JSON body (proxy noise, HTML error page): surfaced as None so the
        # status translator's fallback answers ENGINE_PROTOCOL_ERROR.
        return None
    return parsed
