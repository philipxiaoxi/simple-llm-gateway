from __future__ import annotations

import json
from urllib.parse import urlencode

CCS_SWITCH_TARGETS: tuple[tuple[str, str, bool, bool], ...] = (
    # app, label, include /v1, needs_dialog
    ("claude", "Claude Code", False, True),
    ("opencode", "OpenCode", True, True),
    ("codex", "Codex", True, True),
    ("grokbuild", "Grok", True, True),
)


def gateway_endpoint(app_base_url: str, include_openai_v1: bool) -> str:
    root = app_base_url.rstrip("/")
    if include_openai_v1:
        return f"{root}/v1"
    return root


def parse_models_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def build_ccswitch_url(
    *,
    app: str,
    name: str,
    endpoint: str,
    api_key: str,
    model: str | None = None,
    haiku_model: str | None = None,
    sonnet_model: str | None = None,
    opus_model: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    params: dict[str, str] = {
        "resource": "provider",
        "app": app,
        "name": name,
        "endpoint": endpoint,
        "apiKey": api_key,
    }
    if model:
        params["model"] = model
    if haiku_model:
        params["haikuModel"] = haiku_model
    if sonnet_model:
        params["sonnetModel"] = sonnet_model
    if opus_model:
        params["opusModel"] = opus_model
    if extra:
        params.update(extra)
    return "ccswitch://v1/import?" + urlencode(params)


def build_ccswitch_url_for_app(
    *,
    app: str,
    app_base_url: str,
    display_name: str,
    api_key: str,
    models: list[str],
    model: str | None = None,
    haiku_model: str | None = None,
    sonnet_model: str | None = None,
    opus_model: str | None = None,
) -> str:
    include_v1 = app != "claude"
    endpoint = gateway_endpoint(app_base_url, include_v1)
    chosen = model
    if not chosen and models:
        raise ValueError("请选择要导入的模型")
    return build_ccswitch_url(
        app=app,
        name=display_name,
        endpoint=endpoint,
        api_key=api_key,
        model=chosen,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        opus_model=opus_model,
    )


def describe_ccswitch_targets(app_base_url: str, display_name: str, api_key: str, models: list[str]) -> list[dict]:
    items: list[dict] = []
    for app, label, _include_v1, needs_dialog in CCS_SWITCH_TARGETS:
        item: dict = {"app": app, "label": label, "needs_dialog": needs_dialog}
        if not needs_dialog:
            item["url"] = build_ccswitch_url_for_app(
                app=app,
                app_base_url=app_base_url,
                display_name=display_name,
                api_key=api_key,
                models=models,
            )
        items.append(item)
    return items
