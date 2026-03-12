from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import quote

import httpx

from reflexor.cli.client.protocol import ReplayModeStr
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.domain.models_event import Event


@dataclass(slots=True)
class ApiClient:
    """HTTP-backed CLI client for remote operation via the Reflexor API."""

    base_url: str
    admin_api_key: str | None = None
    http: httpx.AsyncClient | None = None
    _owns_http: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized_base_url = self.base_url.strip()
        parsed_base_url = httpx.URL(normalized_base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.host:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parsed_base_url.query or parsed_base_url.fragment:
            raise ValueError("base_url must not include query params or fragments")
        self.base_url = str(parsed_base_url).rstrip("/")
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=10.0)
            self._owns_http = True

    def _url(self, path: str) -> str:
        normalized_base = self.base_url.rstrip("/")
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{normalized_base}{normalized_path}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.admin_api_key:
            headers["X-API-Key"] = self.admin_api_key
        return headers

    def _path_segment(self, value: str, *, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must be non-empty")
        return quote(normalized, safe="")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> object:
        http = self.http
        assert http is not None
        filtered_params = None
        if params is not None:
            filtered_params = {key: value for key, value in params.items() if value is not None}
        if json_body is None:
            response = await http.request(
                method=method,
                url=self._url(path),
                headers=self._headers(),
                params=filtered_params,
            )
        else:
            response = await http.request(
                method=method,
                url=self._url(path),
                headers=self._headers(),
                params=filtered_params,
                json=json_body,
            )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise httpx.RemoteProtocolError("response did not contain valid JSON") from exc

    async def submit_event(self, event: Event) -> dict[str, object]:
        payload = {
            "type": event.type,
            "source": event.source,
            "payload": event.payload,
            "dedupe_key": event.dedupe_key,
            "received_at_ms": int(event.received_at_ms),
        }
        data = await self._request_json("POST", "/v1/events", json_body=payload)
        return cast(dict[str, object], data)

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        since_ms: int | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if status is not None:
            params["status"] = str(status)
        if since_ms is not None:
            params["since_ms"] = int(since_ms)
        data = await self._request_json("GET", "/v1/runs", params=params)
        return cast(dict[str, object], data)

    async def get_run(self, run_id: str) -> dict[str, object]:
        path = f"/v1/runs/{self._path_segment(run_id, field_name='run_id')}"
        data = await self._request_json("GET", path)
        return cast(dict[str, object], data)

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if run_id is not None:
            params["run_id"] = run_id
        if status is not None:
            params["status"] = str(status)
        data = await self._request_json("GET", "/v1/tasks", params=params)
        return cast(dict[str, object], data)

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if status is not None:
            params["status"] = str(status)
        if run_id is not None:
            params["run_id"] = run_id
        data = await self._request_json("GET", "/v1/approvals", params=params)
        return cast(dict[str, object], data)

    async def approve(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]:
        json_body = None if decided_by is None else {"decided_by": decided_by}
        data = await self._request_json(
            "POST",
            f"/v1/approvals/{self._path_segment(approval_id, field_name='approval_id')}/approve",
            json_body=json_body,
        )
        return cast(dict[str, object], data)

    async def deny(self, approval_id: str, *, decided_by: str | None = None) -> dict[str, object]:
        json_body = None if decided_by is None else {"decided_by": decided_by}
        data = await self._request_json(
            "POST",
            f"/v1/approvals/{self._path_segment(approval_id, field_name='approval_id')}/deny",
            json_body=json_body,
        )
        return cast(dict[str, object], data)

    async def list_tools(self) -> list[dict[str, object]]:
        raise NotImplementedError("list_tools is not exposed via the API yet")

    async def health(self) -> dict[str, object]:
        data = await self._request_json("GET", "/healthz")
        return cast(dict[str, object], data)

    async def aclose(self) -> None:
        if not self._owns_http:
            return
        http = self.http
        if http is None:
            return
        await http.aclose()
        self.http = None
        self._owns_http = False

    async def export_run_packet(
        self,
        run_id: str,
        out_path: str | Path,
        *,
        include_tasks: bool = True,
    ) -> dict[str, object]:
        _ = (run_id, out_path, include_tasks)
        raise NotImplementedError("run export is not exposed via the API yet")

    async def import_run_packet(
        self,
        path: str | Path,
        *,
        parent_run_id: str | None = None,
    ) -> dict[str, object]:
        _ = (path, parent_run_id)
        raise NotImplementedError("run import is not exposed via the API yet")

    async def replay_run_packet(
        self,
        path: str | Path,
        *,
        mode: ReplayModeStr,
    ) -> dict[str, object]:
        _ = (path, mode)
        raise NotImplementedError("run replay is not exposed via the API yet")

    async def list_suppressions(self, *, limit: int, offset: int) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        data = await self._request_json("GET", "/v1/suppressions", params=params)
        return cast(dict[str, object], data)

    async def clear_suppression(
        self, signature_hash: str, *, cleared_by: str | None = None
    ) -> dict[str, object]:
        json_body = None if cleared_by is None else {"cleared_by": cleared_by}
        data = await self._request_json(
            "POST",
            (
                "/v1/suppressions/"
                f"{self._path_segment(signature_hash, field_name='signature_hash')}/clear"
            ),
            json_body=json_body,
        )
        return cast(dict[str, object], data)


__all__ = ["ApiClient"]
