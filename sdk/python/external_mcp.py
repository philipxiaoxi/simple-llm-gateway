"""Minimal outbound external-MCP worker SDK (Python 3.11+, httpx)."""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, platform_url: str, credential: str, *, version: str = "1.0.0", description: str = ""):
        url = urlsplit(platform_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment or (
            url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("Use an HTTPS platform URL (HTTP is allowed only for local development)")
        self.url = platform_url.rstrip("/") + "/v1/external-mcp"
        self.credential = credential
        self.instance_id = str(uuid.uuid4())
        self.version, self.description = version, description
        self.tools: dict[str, tuple[dict, Any]] = {}
        self.lease: dict = {}
        self._upload_context: ContextVar = ContextVar("external_mcp_upload", default=None)

    def tool(self, name: str, description: str, input_schema: dict, **metadata: Any):
        def register(handler):
            if name in self.tools:
                raise ValueError(f"Duplicate tool: {name}")
            self.tools[name] = ({**metadata, "name": name, "description": description, "input_schema": input_schema}, handler)
            return handler
        return register

    async def _request(self, client, method, path, *, registration=False, **kwargs):
        token = self.credential if registration else self.lease["lease_token"]
        response = await client.request(method, self.url + path, headers={"Authorization": "Bearer " + token}, **kwargs)
        response.raise_for_status()
        return response.json() if response.status_code != 204 else None

    async def upload_file(self, path: str | Path, *, filename: str | None = None,
                          content_type: str = "application/octet-stream") -> str:
        """Upload a local file and attach it to the current successful tool result."""
        context = self._upload_context.get()
        if context is None:
            raise RuntimeError("Upload files from an async tool handler")
        client, root, call, artifacts = context
        source = Path(path)
        size = source.stat().st_size
        slot = await self._request(client, "POST", root + f"/calls/{call['call_id']}/artifacts", json={
            "dispatch_id": call["dispatch_id"], "filename": filename or source.name,
            "content_type": content_type, "size_bytes": size,
        })
        with source.open("rb") as stream:
            async def chunks():
                while chunk := await asyncio.to_thread(stream.read, 64 * 1024):
                    yield chunk

            response = await client.put(slot["upload_url"], content=chunks(), headers={
                "Authorization": "Bearer " + slot["upload_token"], "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
            })
            response.raise_for_status()
        artifacts.append(slot["artifact_id"])
        return slot["artifact_id"]

    async def _heartbeat(self, client, root):
        while True:
            await asyncio.sleep(self.lease["heartbeat_seconds"])
            self.lease = await self._request(client, "PUT", root + "/lease")

    async def _work(self, client, root):
        while True:
            call = await self._request(client, "POST", root + "/claims")
            if call is None:
                continue
            report: dict = {"dispatch_id": call["dispatch_id"]}
            try:
                remaining = (datetime.fromisoformat(call["deadline_at"]) - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    handler = self.tools[call["operation"]][1]
                    artifacts: list[str] = []
                    if inspect.iscoroutinefunction(handler):
                        token = self._upload_context.set((client, root, call, artifacts))
                        try:
                            value = await handler(**call["arguments"])
                        finally:
                            self._upload_context.reset(token)
                    else:
                        value = await asyncio.to_thread(handler, **call["arguments"])
                    if not isinstance(value, dict):
                        raise TypeError("Tool must return a JSON object")
                    report.update(ok=True, result=value)
                    if artifacts:
                        report["artifact_ids"] = artifacts
            except TimeoutError:
                report.update(ok=False, error={"type": "timeout"})
            except Exception:
                report.update(ok=False, error={"type": "external_error"})
            path = root + f"/calls/{call['call_id']}/result"
            for attempt in range(2):
                try:
                    await self._request(client, "PUT", path, json=report)
                    break
                except httpx.TransportError:
                    if attempt:
                        raise
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 410 and exc.response.json().get("detail", {}).get("error", {}).get("type") == "call_finished":
                        break
                    raise

    async def run(self):
        root = f"/instances/{self.instance_id}"
        manifest = {"protocol_version": "external-mcp/v1", "instance_id": self.instance_id,
                    "version": self.version, "description": self.description,
                    "tools": [definition for definition, _ in self.tools.values()]}
        delay = 1.0
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            try:
                while True:
                    tasks = []
                    try:
                        self.lease = await self._request(client, "POST", "/instances", registration=True, json=manifest)
                        delay = 1.0
                        tasks = [asyncio.create_task(self._heartbeat(client, root)), asyncio.create_task(self._work(client, root))]
                        await asyncio.gather(*tasks)
                    except httpx.HTTPStatusError as exc:
                        code = exc.response.status_code
                        if code in {400, 401, 403, 404, 422} and not tasks:
                            raise
                        logger.warning("External MCP connection interrupted (HTTP %s); retrying", code)
                    except httpx.TransportError:
                        logger.warning("External MCP transport interrupted; retrying")
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    await asyncio.sleep(delay + random.random())
                    delay = min(20, delay * 2)
            finally:
                if self.lease:
                    try:
                        async with asyncio.timeout(3):
                            await self._request(client, "DELETE", root + "/lease")
                    except (httpx.HTTPError, TimeoutError):
                        pass
