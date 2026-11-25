import asyncio
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import update

from app.clock import utcnow
from app.models import ExternalMcpInstance
from app.services.external_mcp import write_session

ADMIN = "/api/admin/mcp/external-services"
WORKER = "/v1/external-mcp/instances"


def test_external_registration_docs_and_dispatch(client, auth_headers):
    openapi = client.get("/v1/external-mcp/openapi.json").json()
    assert WORKER in openapi["paths"]
    assert "Tool" in openapi["components"]["schemas"]
    service = client.post(ADMIN, headers=auth_headers, json={"name": "Echo"}).json()["id"]
    base = f"{ADMIN}/{service}"
    credential = client.post(base + "/credentials", headers=auth_headers, json={"name": "local"}).json()
    reg_headers = {"Authorization": "Bearer " + credential["token"]}
    instance = str(uuid4())
    manifest = {"protocol_version": "external-mcp/v1", "instance_id": instance, "version": "1", "tools": [{
        "name": "echo", "description": "Echo", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}},
        "required": ["text"]}, "example": {"text": "hello"},
    }]}
    assert client.post(WORKER, json=manifest).status_code == 401
    registration = client.post(WORKER, headers=reg_headers, json=manifest)
    assert registration.status_code == 201, registration.text
    lease = registration.json()
    assert client.post(WORKER, headers=reg_headers, json=manifest).json()["lease_epoch"] == lease["lease_epoch"]
    worker_headers = {"Authorization": "Bearer " + lease["lease_token"]}
    root = f"{WORKER}/{instance}"
    cid = f"ext_{service}"
    tool_name = cid + "_echo"

    def make_key(name, capabilities):
        response = client.post("/api/admin/mcp/keys", headers=auth_headers,
                               json={"name": name, "capability_ids": capabilities})
        assert response.status_code in {200, 201}, response.text
        return response.json()

    key = make_key("authorized", [cid, "knowledge"])
    other = make_key("not-authorized", ["knowledge"])

    def docs(key):
        return client.get(f"/api/admin/mcp/keys/{key['id']}/integration", headers=auth_headers).json()["capabilities"]

    entry = next(row for row in docs(key) if row["capability_id"] == cid)
    assert entry["integration"]["rest_endpoints"][0]["example_body"] == {"text": "hello"}
    assert not any(row["capability_id"] == cid for row in docs(other))

    def headers(key):
        return {"Authorization": "Bearer " + key["key"], "Accept": "application/json, text/event-stream"}

    def mcp(method, key, params=None):
        response = client.post("/mcp", headers=headers(key), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        assert response.status_code == 200, response.text
        return response.json()["result"]

    assert tool_name in {tool["name"] for tool in mcp("tools/list", key)["tools"]}
    assert tool_name not in {tool["name"] for tool in mcp("tools/list", other)["tools"]}
    assert mcp("tools/call", other, {"name": tool_name, "arguments": {"text": "hello"}})["isError"]
    path = f"/v1/capabilities/{cid}/echo"
    assert client.post(path, headers=headers(other), json={"text": "hello"}).status_code == 403
    assert client.post(path, headers=headers(key), json={}).status_code == 422

    for invoke in (
        lambda: client.post(path, headers=headers(key), json={"text": "hello"}).json(),
        lambda: mcp("tools/call", key, {"name": tool_name, "arguments": {"text": "hello"}}),
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            call_response = pool.submit(invoke)
            claimed = client.post(root + "/claims?wait=4", headers=worker_headers)
            assert claimed.status_code == 200, claimed.text
            call = claimed.json()
            assert client.post(root + "/claims?wait=0", headers=worker_headers).status_code == 204
            report = {"dispatch_id": call["dispatch_id"], "ok": True, "result": call["arguments"]}
            result_path = root + f"/calls/{call['call_id']}/result"
            assert client.put(result_path, headers=worker_headers, json=report).status_code == 204
            assert client.put(result_path, headers=worker_headers, json=report).status_code == 204
            assert "hello" in str(call_response.result(timeout=5))

    with write_session() as db:
        db.execute(update(ExternalMcpInstance).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.put(root + "/lease", headers=worker_headers).status_code == 410
    assert client.post(path, headers=headers(key), json={"text": "hello"}).status_code == 503
    assert mcp("tools/call", key, {"name": tool_name, "arguments": {"text": "hello"}})["isError"]
    assert tool_name not in {tool["name"] for tool in mcp("tools/list", key)["tools"]}
    assert next(row for row in docs(key) if row["capability_id"] == cid)["status"] == "offline"

    resumed = client.post(WORKER, headers=reg_headers, json=manifest).json()
    assert resumed["lease_epoch"] != lease["lease_epoch"]
    assert client.delete(base + "/registration", headers=auth_headers).status_code == 204
    assert client.put(root + "/lease", headers={"Authorization": "Bearer " + resumed["lease_token"]}).status_code == 410
    assert client.post(WORKER, headers=reg_headers, json=manifest).status_code == 201
    changed = {**manifest, "version": "2"}
    assert client.post(WORKER, headers=reg_headers, json=changed).status_code == 409
    pending = client.get(ADMIN, headers=auth_headers).json()["items"][0]
    assert pending["version"] == "1" and pending["pending_manifest"]["version"] == "2"
    assert client.post(base + "/manifest/approve", headers=auth_headers,
                       json={"revision": pending["pending_revision"]}).status_code == 204
    assert client.post(WORKER, headers=reg_headers, json=changed).status_code == 201
    assert client.post(base + f"/credentials/{credential['id']}/renew", headers=auth_headers, json={"days": 7}).status_code == 200
    metadata = client.get(base + "/credentials", headers=auth_headers).json()["items"]
    assert metadata[0]["expires_at"] > credential["expires_at"] and "token" not in metadata[0]
    assert client.delete(base + f"/credentials/{credential['id']}", headers=auth_headers).status_code == 204
    assert client.post(WORKER, headers=reg_headers, json=changed).status_code == 401
    assert client.post(path, headers=headers(key), json={"text": "hello"}).status_code == 503


def test_python_sdk_reregisters_until_credential_revoked(client, auth_headers, monkeypatch):
    from app.routers import external_mcp as routes

    spec = importlib.util.spec_from_file_location("worker_sdk", Path(__file__).parents[2] / "sdk/python/external_mcp.py")
    assert spec is not None and spec.loader is not None
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)
    monkeypatch.setattr(routes.ext, "HEARTBEAT_SECONDS", 0.05)
    service = client.post(ADMIN, headers=auth_headers, json={"name": "SDK sample"}).json()["id"]
    base = f"{ADMIN}/{service}"
    credential = client.post(base + "/credentials", headers=auth_headers, json={"name": "SDK"}).json()
    worker = sdk.Worker("http://127.0.0.1:8000", credential["token"])

    @worker.tool("echo", "Echo", {"type": "object"})
    async def echo(**data):
        return data

    registrations = asyncio.Queue()

    async def transport(req):
        path = req.url.raw_path.decode()
        if path.endswith("/claims"):
            path += "?wait=0"
            await asyncio.sleep(0.02)
        response = await asyncio.to_thread(client.request, req.method, path, headers=dict(req.headers), content=req.content)
        if path == WORKER and response.is_success:
            registrations.put_nowait(response.json()["lease_epoch"])
        return httpx.Response(response.status_code, content=response.content)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sdk.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs))

    async def run():
        task = asyncio.create_task(worker.run())
        async def registered():
            event = asyncio.create_task(registrations.get())
            try:
                done, _ = await asyncio.wait({task, event}, timeout=6, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    await task
                assert event in done, "SDK did not register"
                return event.result()
            finally:
                event.cancel()
                await asyncio.gather(event, return_exceptions=True)
        try:
            first = await registered()
            response = await asyncio.to_thread(client.delete, base + "/registration", headers=auth_headers)
            assert response.status_code == 204
            second = await registered()
            assert first != second
            response = await asyncio.to_thread(client.delete, base + f"/credentials/{credential['id']}", headers=auth_headers)
            assert response.status_code == 204
            try:
                await asyncio.wait_for(task, 5)
            except httpx.HTTPStatusError as exc:
                assert exc.response.status_code == 401
            else:
                raise AssertionError("revoked worker must stop registering")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
