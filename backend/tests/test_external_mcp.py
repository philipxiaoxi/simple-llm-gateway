import asyncio
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from sqlalchemy import update

from app.clock import utcnow
from app.config import get_settings
from app.models import ExternalMcpInstance
from app.services import external_mcp_artifacts as files
from app.services.external_mcp import write_session

ADMIN = "/api/admin/mcp/external-services"
WORKER = "/v1/external-mcp/instances"


def test_external_integration_kit(client, auth_headers):
    path = ADMIN + "/integration-kit"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "external-mcp-sdk.zip" in response.headers["content-disposition"]
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "README.md", "external-mcp-services.md", "python/external_mcp.py", "python/echo.py", "python/bridge.py",
            "python/requirements.txt", "nodejs/external-mcp.mjs", "nodejs/echo.mjs", "openapi.json",
        }
        assert b"(external-mcp-services.md)" in archive.read("README.md")
        paths = json.loads(archive.read("openapi.json"))["paths"]
        assert WORKER in paths
        assert "/v1/external-mcp/artifacts/{artifact_id}/upload" in paths


def test_heartbeat_history_buckets(client, auth_headers, monkeypatch):
    from app.routers import external_mcp as routes

    now = datetime(2026, 11, 25, 10, 14)
    monkeypatch.setattr(routes, "utcnow", lambda: now)
    service = client.post(ADMIN, headers=auth_headers, json={"name": "History demo"}).json()["id"]
    base = f"{ADMIN}/{service}"
    url = base + "/heartbeats"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth_headers).json()["counts"] == [None] * 60
    token = client.post(base + "/credentials", headers=auth_headers, json={"name": "test"}).json()["token"]
    instance = str(uuid4())
    registered = client.post(WORKER, headers={"Authorization": "Bearer " + token}, json={
        "protocol_version": "external-mcp/v1", "instance_id": instance, "version": "1",
        "tools": [{"name": "echo", "description": "Echo", "input_schema": {"type": "object"}}],
    })
    assert registered.status_code == 201
    now += timedelta(seconds=15)
    lease = {"Authorization": "Bearer " + registered.json()["lease_token"]}
    assert client.put(f"{WORKER}/{instance}/lease", headers=lease).status_code == 200
    now += timedelta(minutes=1)
    assert client.get(url, headers=auth_headers).json()["counts"] == [None] * 58 + [2, 0]
    history = client.get(url + "?period=24h", headers=auth_headers).json()
    assert history["bucket_seconds"] == 900
    assert history["counts"] == [None] * 94 + [2, 0]


def test_external_registration_docs_and_dispatch(client, auth_headers):
    openapi = client.get("/v1/external-mcp/openapi.json").json()
    assert WORKER in openapi["paths"]
    assert "Tool" in openapi["components"]["schemas"]
    service = client.post(ADMIN, headers=auth_headers, json={"name": "Echo"}).json()["id"]
    base = f"{ADMIN}/{service}"
    credential = client.post(base + "/credentials", headers=auth_headers, json={"name": "local", "days": 30}).json()
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
    assert client.patch(base, headers=auth_headers, json={"name": "Echo renamed"}).json() == {"id": service, "name": "Echo renamed"}
    assert next(row for row in docs(key) if row["capability_id"] == cid)["name"] == "Echo renamed"
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
    permanent = client.post(base + f"/credentials/{credential['id']}/renew", headers=auth_headers, json={"days": None})
    assert permanent.status_code == 200 and permanent.json()["expires_at"] is None
    assert client.delete(base + f"/credentials/{credential['id']}", headers=auth_headers).status_code == 204
    assert client.post(WORKER, headers=reg_headers, json=changed).status_code == 401
    assert client.post(path, headers=headers(key), json={"text": "hello"}).status_code == 503
    second = client.post(base + "/credentials", headers=auth_headers, json={"name": "second"}).json()
    second_headers = {"Authorization": "Bearer " + second["token"]}
    assert client.post(WORKER, headers=second_headers, json=changed).status_code == 201
    assert client.delete(base, headers=auth_headers).status_code == 204
    assert client.delete(base, headers=auth_headers).status_code == 204
    assert client.get(ADMIN, headers=auth_headers).json()["items"] == []
    assert client.get(base + "/credentials", headers=auth_headers).status_code == 404
    assert client.post(WORKER, headers=second_headers, json=changed).status_code == 401
    assert client.post(path, headers=headers(key), json={"text": "hello"}).status_code == 404
    assert not any(row["capability_id"] == cid for row in docs(key))


def test_python_sdk_reregisters_until_credential_revoked(client, auth_headers, monkeypatch, tmp_path):
    from app.routers import external_mcp as routes

    spec = importlib.util.spec_from_file_location("worker_sdk", Path(__file__).parents[2] / "sdk/python/external_mcp.py")
    assert spec is not None and spec.loader is not None
    sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sdk)
    monkeypatch.setattr(routes.ext, "HEARTBEAT_SECONDS", 0.05)
    service = client.post(ADMIN, headers=auth_headers, json={"name": "SDK sample"}).json()["id"]
    base = f"{ADMIN}/{service}"
    credential = client.post(base + "/credentials", headers=auth_headers, json={"name": "SDK"}).json()
    assert credential["expires_at"] is None
    worker = sdk.Worker("http://127.0.0.1:8000", credential["token"])
    monkeypatch.setattr(get_settings(), "app_base_url", "http://127.0.0.1:8000")
    fixture = tmp_path / "example.pptx"
    fixture.write_bytes(b"test binary\x00")

    @worker.tool("echo", "Echo", {"type": "object"})
    async def echo(**data):
        if data.get("file"):
            await worker.upload_file(fixture)
        return data

    registrations = asyncio.Queue()

    async def transport(req):
        path = req.url.raw_path.decode()
        if path.endswith("/claims"):
            path += "?wait=0"
            await asyncio.sleep(0.02)
        response = await asyncio.to_thread(client.request, req.method, path, headers=dict(req.headers), content=await req.aread())
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
            cid = f"ext_{service}"
            key = (await asyncio.to_thread(client.post, "/api/admin/mcp/keys", headers=auth_headers,
                                           json={"name": "SDK test", "capability_ids": [cid]})).json()["key"]
            delivered = await asyncio.to_thread(client.post, f"/v1/capabilities/{cid}/echo",
                                                headers={"Authorization": "Bearer " + key}, json={"file": True})
            assert delivered.status_code == 200, delivered.text
            url = delivered.json()["artifacts"][0]["download_url"]
            assert (await asyncio.to_thread(client.get, url)).content == fixture.read_bytes()
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


@pytest.mark.parametrize("transport", ["rest", "mcp"])
def test_artifact_delivery(client, auth_headers, monkeypatch, transport):
    monkeypatch.setattr(get_settings(), "app_base_url", "https://testserver")
    service = client.post(ADMIN, headers=auth_headers, json={"name": "Slide deck"}).json()["id"]
    credential = client.post(f"{ADMIN}/{service}/credentials", headers=auth_headers, json={"name": "SDK"}).json()["token"]
    instance = str(uuid4())
    registration = client.post(WORKER, headers={"Authorization": "Bearer " + credential}, json={
        "protocol_version": "external-mcp/v1", "instance_id": instance, "version": "1",
        "tools": [{"name": "deck", "description": "Make a slide deck", "input_schema": {"type": "object"},
                   "output_schema": {"type": "object", "additionalProperties": False}}],
    })
    worker = {"Authorization": "Bearer " + registration.json()["lease_token"]}
    cap = f"ext_{service}"
    key = client.post("/api/admin/mcp/keys", headers=auth_headers, json={"name": "Slides", "capability_ids": [cap]}).json()["key"]
    consumer = {"Authorization": "Bearer " + key, "Accept": "application/json, text/event-stream"}
    root = f"{WORKER}/{instance}"
    pptx = b"PK\x03\x04example binary\x00\xff"
    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def invoke():
        if transport == "rest":
            return client.post(f"/v1/capabilities/{cap}/deck", headers=consumer, json={})
        return client.post("/mcp", headers=consumer, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                            "params": {"name": f"{cap}_deck", "arguments": {}}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        output = pool.submit(invoke)
        call = client.post(root + "/claims?wait=4", headers=worker).json()
        call_path = root + f"/calls/{call['call_id']}"
        spec = {"dispatch_id": call["dispatch_id"], "filename": "演示.pptx", "content_type": mime, "size_bytes": len(pptx)}
        assert client.post(call_path + "/artifacts", headers=worker, json={**spec, "dispatch_id": "0" * 32}).status_code == 404
        assert client.post(call_path + "/artifacts", headers=worker, json={**spec, "filename": "../test"}).status_code == 422
        assert client.post(call_path + "/artifacts", headers=worker, json={**spec, "size_bytes": files.MAX_SIZE + 1}).status_code == 422
        created = client.post(call_path + "/artifacts", headers=worker, json=spec)
        assert created.status_code == 201, created.text
        slot = created.json()
        artifact_id = slot["artifact_id"]
        upload = slot["upload_url"]
        report = {"dispatch_id": call["dispatch_id"], "ok": True, "result": {}, "artifact_ids": [artifact_id]}
        assert client.put(call_path + "/result", headers=worker, json=report).status_code == 422
        upload_headers = {"Authorization": "Bearer " + slot["upload_token"]}
        assert client.put(upload, content=pptx, headers=worker).status_code == 404
        assert client.put(upload, content=pptx[:-1], headers=upload_headers).status_code == 422
        assert client.put(upload, content=iter([pptx, b"overflow"]), headers=upload_headers).status_code == 413
        assert client.put(upload, content=pptx, headers=upload_headers).status_code == 204
        assert client.put(upload, content=pptx, headers=upload_headers).status_code == 409

        other_output = pool.submit(invoke)
        other = client.post(root + "/claims?wait=4", headers=worker).json()
        other_path = root + f"/calls/{other['call_id']}/result"
        assert client.put(other_path, headers=worker, json={**report, "dispatch_id": other["dispatch_id"]}).status_code == 422
        assert client.put(other_path, headers=worker, json={"dispatch_id": other["dispatch_id"], "ok": True, "result": {}}).status_code == 204
        assert other_output.result(timeout=5).status_code == 200
        assert client.put(call_path + "/result", headers=worker, json=report).status_code == 204
        assert client.put(call_path + "/result", headers=worker, json=report).status_code == 204
        response = output.result(timeout=5)
        assert response.status_code == 200, response.text

    if transport == "mcp":
        result = response.json()["result"]
        envelope = result["structuredContent"]
        url = envelope["artifacts"][0]["download_url"]
        assert any(item["type"] == "resource_link" and item["uri"] == url for item in result["content"])
        assert any(item["type"] == "text" and url in item["text"] for item in result["content"])
    else:
        envelope = response.json()
    artifact = envelope["artifacts"][0]
    assert envelope["result"] == {}
    assert artifact["filename"] == "演示.pptx" and artifact["content_type"] == mime
    url = artifact["download_url"]
    assert url.startswith("https://testserver/") and slot["upload_token"] not in url
    assert client.get(url.split("?")[0]).status_code == 404
    assert client.get(url.replace(artifact_id, "0" * 32)).status_code == 404
    downloaded = client.get(url)
    assert downloaded.content == pptx
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert client.head(url).headers["content-length"] == str(len(pptx))

    expiry = datetime.fromisoformat(artifact["expires_at"]).replace(tzinfo=None)
    from app.routers import external_mcp as routes

    monkeypatch.setattr(routes, "utcnow", lambda: expiry + timedelta(seconds=1))
    assert client.get(url).status_code == 404
    with write_session() as db:
        files.cleanup_expired(db, expiry + timedelta(seconds=1))
    assert not files.file_path(artifact_id).exists()
