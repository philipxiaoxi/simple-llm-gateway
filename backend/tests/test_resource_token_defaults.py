from app.config import Settings


def test_resource_token_ttl_defaults_and_override(monkeypatch):
    names = (
        "transfer_token_ttl_seconds",
        "douyin_download_token_ttl_seconds",
        "references_image_token_ttl_seconds",
        "info_media_token_ttl_seconds",
        "generation_token_ttl_seconds",
    )
    for name in names:
        monkeypatch.delenv(name.upper(), raising=False)
    defaults = Settings(_env_file=None)
    assert all(getattr(defaults, name) == 3600 for name in names)
    for name in names:
        monkeypatch.setenv(name.upper(), "7200")
    overridden = Settings(_env_file=None)
    assert all(getattr(overridden, name) == 7200 for name in names)
