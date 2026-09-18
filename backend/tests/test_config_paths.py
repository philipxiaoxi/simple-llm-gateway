from __future__ import annotations

from app.config import Settings


def _settings(tmp_path, monkeypatch, **env) -> Settings:
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-32chars-minimum")
    monkeypatch.delenv("MCP_CHROMA_PATH", raising=False)
    monkeypatch.delenv("MCP_KNOWLEDGE_JOBS_PATH", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "gateway.db"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_knowledge_paths_follow_database_dir(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    assert settings.resolved_chroma_path == (tmp_path / "data" / "chroma").resolve()
    assert settings.resolved_knowledge_jobs_path == (tmp_path / "data" / "knowledge_jobs").resolve()
    assert settings.resolved_chroma_path.is_dir()


def test_relative_configured_path_anchored_to_data_dir(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch, MCP_CHROMA_PATH="chroma")
    assert settings.resolved_chroma_path == (tmp_path / "data" / "chroma").resolve()


def test_absolute_configured_path_is_kept(tmp_path, monkeypatch) -> None:
    target = tmp_path / "elsewhere" / "vectors"
    settings = _settings(tmp_path, monkeypatch, MCP_CHROMA_PATH=str(target))
    assert settings.resolved_chroma_path == target.resolve()
