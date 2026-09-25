from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import online_agent


def test_tab_order_keeps_agent_in_the_middle() -> None:
    from frontend_tab_order import tab_paths

    assert tab_paths()[2] == "/online-agent"


def test_sync_writes_gateway_provider_without_upstream_secret(client: TestClient, auth_headers: dict[str, str], tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(online_agent, "root_dir", lambda: tmp_path)
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "Deep", "provider": "deepseek", "api_key": "sk-upstream-secret"},
    )
    assert created.status_code == 200, created.text
    response = client.post("/api/admin/online-agent/sync", headers=auth_headers)
    assert response.status_code == 200, response.text
    config = (tmp_path / "opencode.json").read_text(encoding="utf-8")
    assert '"gateway"' in config
    assert "http://127.0.0.1:8000/v1" in config
    assert "sk-upstream-secret" not in config
    assert response.json()["skills"] == [] or all(not item["enabled"] for item in response.json()["skills"])
    assert all(not item["enabled"] for item in response.json()["mcp"])


def test_skill_toggle_links_directory(client: TestClient, auth_headers: dict[str, str], tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(online_agent, "root_dir", lambda: tmp_path)
    from app.db import get_session_factory
    from app.models import Skill

    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    session = get_session_factory()()
    skill = Skill(slug="demo", name="Demo", storage_dir="demo", skill_md="demo")
    session.add(skill)
    session.commit()
    skill_id = skill.id
    session.close()
    monkeypatch.setattr("app.services.skills.skill_dir", lambda _skill: source)

    disabled = client.post("/api/admin/online-agent/sync", headers=auth_headers)
    assert disabled.status_code == 200
    assert not (tmp_path / "skills" / "demo").exists()

    enabled = client.put(
        "/api/admin/online-agent/skills",
        headers=auth_headers,
        json={"target_id": str(skill_id), "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert (tmp_path / "skills" / "demo").is_symlink()


def test_prompt_uses_same_session_and_generates_title_once(client: TestClient, auth_headers: dict[str, str], tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(online_agent, "root_dir", lambda: tmp_path)
    monkeypatch.setattr(online_agent, "process_running", lambda: True)
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path))

        class Response:
            status_code = 200
            text = ""

            def json(self):
                if path == "/session":
                    return {"id": "ses_1", "title": "新会话"}
                return {}

        return Response()

    monkeypatch.setattr(online_agent, "_request", fake_request)
    created = client.post("/api/admin/online-agent/sessions", headers=auth_headers)
    assert created.status_code == 200, created.text
    first = client.post(
        "/api/admin/online-agent/sessions/ses_1/prompt",
        headers=auth_headers,
        json={"text": "帮我看看登录问题", "model": "deepseek-chat"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/admin/online-agent/sessions/ses_1/prompt",
        headers=auth_headers,
        json={"text": "继续", "model": "deepseek-chat"},
    )
    assert second.status_code == 200
    assert calls.count(("PATCH", "/session/ses_1")) == 1
    assert ("POST", "/session/ses_1/prompt_async") in calls
    assert not (tmp_path / "workspaces" / "ses_1" / ".git").exists()
