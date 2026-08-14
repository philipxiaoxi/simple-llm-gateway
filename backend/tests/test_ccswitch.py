from urllib.parse import parse_qs, urlparse

from app.services.ccswitch import (
    build_ccswitch_url,
    build_ccswitch_url_for_app,
    describe_ccswitch_targets,
    gateway_endpoint,
)


def test_claude_endpoint_has_no_v1() -> None:
    assert gateway_endpoint("http://127.0.0.1:8000/", False) == "http://127.0.0.1:8000"


def test_openai_style_endpoint_appends_v1() -> None:
    assert gateway_endpoint("http://127.0.0.1:8000", True) == "http://127.0.0.1:8000/v1"


def test_build_ccswitch_url_matches_official_protocol() -> None:
    url = build_ccswitch_url(
        app="claude",
        name="中转台 Key",
        endpoint="http://127.0.0.1:8000",
        api_key="sk-demo",
        model="glm-5.3",
        haiku_model="fast",
    )
    query = parse_qs(urlparse(url).query)
    assert url.startswith("ccswitch://v1/import?")
    assert query["resource"] == ["provider"]
    assert query["app"] == ["claude"]
    assert query["apiKey"] == ["sk-demo"]
    assert query["model"] == ["glm-5.3"]
    assert query["haikuModel"] == ["fast"]


def test_opencode_requires_explicit_model() -> None:
    try:
        build_ccswitch_url_for_app(
            app="opencode",
            app_base_url="http://127.0.0.1:8000",
            display_name="同事A",
            api_key="sk-abc",
            models=["glm-5.3", "kimi-k2.6"],
        )
        raise AssertionError("should require model")
    except ValueError as error:
        assert "选择" in str(error)


def test_opencode_deeplink_uses_chosen_model() -> None:
    url = build_ccswitch_url_for_app(
        app="opencode",
        app_base_url="http://127.0.0.1:8000",
        display_name="同事A",
        api_key="sk-abc",
        models=["glm-5.3", "kimi-k2.6"],
        model="kimi-k2.6",
    )
    query = parse_qs(urlparse(url).query)
    assert query["app"] == ["opencode"]
    assert query["model"] == ["kimi-k2.6"]
    assert query["endpoint"] == ["http://127.0.0.1:8000/v1"]
    assert "config" not in query


def test_dialog_apps_require_explicit_model() -> None:
    try:
        build_ccswitch_url_for_app(
            app="claude",
            app_base_url="http://127.0.0.1:8000",
            display_name="x",
            api_key="sk-abc",
            models=["a", "b"],
        )
        raise AssertionError("should require model")
    except ValueError as error:
        assert "选择" in str(error)


def test_describe_requires_dialog_for_all_apps() -> None:
    targets = describe_ccswitch_targets("http://127.0.0.1:8000", "n", "sk", ["m1"])
    by_app = {item["app"]: item for item in targets}
    assert by_app["opencode"]["needs_dialog"] is True
    assert "url" not in by_app["opencode"]
    assert by_app["claude"]["needs_dialog"] is True
    assert "url" not in by_app["claude"]
