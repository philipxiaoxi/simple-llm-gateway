from __future__ import annotations

from app.services.ccswitch import build_vscode_config
from app.services.model_caps import (
    CatalogIndex,
    ModelCaps,
    apply_model_override,
    build_catalog_index,
    caps_from_heuristic,
    caps_from_upstream_entry,
    dump_model_records,
    enrich_model_records,
    extract_model_entries,
    match_catalog,
    parse_model_records,
    parse_models_json,
    reset_catalog_cache,
    resolve_auto_caps,
    serialize_record,
)


def test_parse_models_json_accepts_legacy_string_list() -> None:
    assert parse_models_json('["deepseek-chat", "deepseek-reasoner", "deepseek-chat"]') == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]


def test_heuristic_marks_reasoner_and_vision() -> None:
    chat = caps_from_heuristic("deepseek-chat")
    assert chat.reasoning is False
    assert chat.context_window == 128000
    assert chat.input_modalities == ("text",)
    reasoner = caps_from_heuristic("deepseek-reasoner")
    assert reasoner.reasoning is True
    vision = caps_from_heuristic("gpt-4o")
    assert vision.input_modalities == ("text", "image")
    assert caps_from_heuristic("thinking-model").reasoning is True
    assert caps_from_heuristic("rethinking-notes").reasoning is False
    assert caps_from_heuristic("gemini-2.5-pro").input_modalities == ("text", "image")
    assert caps_from_heuristic("gemini-embedding").input_modalities == ("text",)


def test_catalog_match_normalizes_and_votes() -> None:
    index = build_catalog_index(
        {
            "openai": {
                "models": {
                    "gpt-4o": {
                        "id": "gpt-4o",
                        "reasoning": False,
                        "limit": {"context": 128000, "output": 16384},
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            },
            "openrouter": {
                "models": {
                    "openai/gpt-4o": {
                        "id": "openai/gpt-4o",
                        "reasoning": False,
                        "limit": {"context": 128000, "output": 16384},
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            },
            "anthropic": {
                "models": {
                    "claude-opus-4-7": {
                        "id": "claude-opus-4-7",
                        "reasoning": True,
                        "reasoning_options": [{"type": "effort", "values": ["low", "high"]}],
                        "limit": {"context": 1000000, "output": 128000},
                        "modalities": {"input": ["text"], "output": ["text"]},
                    }
                }
            },
        }
    )
    matched = match_catalog("openai/gpt.4o", "openai_generic", index)
    assert matched is not None
    assert matched.context_window == 128000
    assert matched.input_modalities == ("text", "image")
    opus = match_catalog("claude-opus-4.7", "anthropic_generic", index)
    assert opus is not None
    assert opus.reasoning is True
    assert opus.reasoning_efforts == ("low", "high")


def test_catalog_overrides_upstream_and_heuristic() -> None:
    index = CatalogIndex()
    index.by_norm["deepseek-chat"] = ModelCaps(
        context_window=64000,
        max_output_tokens=8000,
        reasoning=False,
        reasoning_efforts=None,
        input_modalities=("text",),
        output_modalities=("text",),
        source="catalog",
    )
    caps = resolve_auto_caps(
        "deepseek-chat",
        upstream=ModelCaps(
            context_window=999,
            max_output_tokens=1,
            reasoning=True,
            reasoning_efforts=("high",),
            input_modalities=("text",),
            output_modalities=("text",),
            source="upstream",
        ),
        catalog=index,
    )
    assert caps.context_window == 64000
    assert caps.source == "catalog"


def test_enrich_keeps_overrides_across_refresh() -> None:
    first = enrich_model_records([{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}], None)
    first = apply_model_override(first, "deepseek-chat", {"context_window": 32000, "reasoning": True})
    stored = dump_model_records(first)
    refreshed = enrich_model_records([{"id": "deepseek-chat"}, {"id": "new-model"}], stored)
    by_id = {record.id: record for record in refreshed}
    assert by_id["deepseek-chat"].overrides["context_window"] == 32000
    assert by_id["deepseek-chat"].effective().context_window == 32000
    assert "new-model" in by_id
    assert "deepseek-reasoner" not in by_id


def test_enabled_survives_refresh_and_clear() -> None:
    first = enrich_model_records([{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}], None)
    first = apply_model_override(first, "deepseek-chat", {"enabled": False})
    stored = dump_model_records(first)
    assert '"enabled": false' in stored
    refreshed = enrich_model_records([{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}], stored)
    by_id = {record.id: record for record in refreshed}
    assert by_id["deepseek-chat"].enabled is False
    assert by_id["deepseek-reasoner"].enabled is True
    cleared = apply_model_override(refreshed, "deepseek-chat", {"clear": True})
    assert next(record.enabled for record in cleared if record.id == "deepseek-chat") is False
    payload = serialize_record(cleared[0])
    assert payload["enabled"] is False
    assert parse_models_json(stored, include_disabled=False) == ["deepseek-reasoner"]


def test_only_effort_values_are_kept() -> None:
    records = enrich_model_records(
        [
            {
                "id": "claude",
                "entry": {
                    "id": "claude",
                    "reasoning": True,
                    "reasoning_options": [{"type": "toggle"}, {"type": "effort", "values": ["low", "high"]}],
                },
            }
        ],
        None,
    )
    assert records[0].effective().reasoning_efforts == ("low", "high")


def test_extract_model_entries_keeps_openrouter_fields() -> None:
    entries = extract_model_entries(
        {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "context_length": 128000,
                    "architecture": {"input_modalities": ["text", "image"]},
                }
            ]
        }
    )
    assert entries[0]["id"] == "openai/gpt-4o"
    assert entries[0]["entry"]["context_length"] == 128000


def test_legacy_records_roundtrip() -> None:
    records = parse_model_records('["chat", {"id": "coder", "reasoning": true, "context_window": 64000}]')
    assert [record.id for record in records] == ["chat", "coder"]
    assert records[1].effective().context_window == 64000
    payload = serialize_record(records[1])
    assert payload["id"] == "coder"
    assert payload["reasoning"] is True


def test_upstream_max_tokens_is_context_when_output_missing() -> None:
    caps = caps_from_upstream_entry(
        {"id": "demo", "max_tokens": 128000, "max_completion_tokens": 8192}
    )
    assert caps is not None
    assert caps.context_window == 128000
    assert caps.max_output_tokens == 8192
    context_only = caps_from_upstream_entry({"id": "demo", "max_tokens": 64000})
    assert context_only is not None
    assert context_only.context_window == 64000
    assert context_only.max_output_tokens == 16000


def test_load_catalog_index_does_not_fetch_on_request(monkeypatch, tmp_path) -> None:
    from app.config import reset_settings
    from app.services.model_caps import _load_catalog_index_cached

    reset_catalog_cache()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    reset_settings()
    called = {"count": 0}

    def boom() -> None:
        called["count"] += 1
        raise AssertionError("request path must not fetch catalog")

    monkeypatch.setattr("app.services.model_caps._fetch_models_dev", boom)
    index = _load_catalog_index_cached()
    assert index.by_norm == {}
    assert called["count"] == 0
    reset_catalog_cache()
    reset_settings()


def test_refresh_catalog_index_populates_request_cache(monkeypatch, tmp_path) -> None:
    import asyncio

    from app.config import reset_settings
    from app.services import model_caps as model_caps_mod
    from app.services.model_caps import _load_catalog_index_cached

    reset_catalog_cache()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    reset_settings()
    payload = {
        "openai": {
            "models": {
                "gpt-4o": {
                    "id": "gpt-4o",
                    "limit": {"context": 128000, "output": 16384},
                    "modalities": {"input": ["text"], "output": ["text"]},
                }
            }
        }
    }
    monkeypatch.setattr(model_caps_mod, "_fetch_models_dev", lambda: payload)
    refreshed = asyncio.run(model_caps_mod.refresh_catalog_index(force=True))
    assert "gpt-4o" in refreshed.by_norm

    called = {"count": 0}

    def boom() -> None:
        called["count"] += 1
        raise AssertionError("request path must not fetch catalog")

    monkeypatch.setattr(model_caps_mod, "_fetch_models_dev", boom)
    assert "gpt-4o" in _load_catalog_index_cached().by_norm
    assert called["count"] == 0
    reset_catalog_cache()
    reset_settings()


def test_vscode_config_uses_enriched_windows() -> None:
    records = [
        serialize_record(item)
        for item in enrich_model_records([{"id": "gpt-4o"}, {"id": "deepseek-reasoner"}], None)
    ]
    records[0]["context_window"] = 128000
    records[0]["max_output_tokens"] = 16384
    records[0]["modalities"] = {"input": ["text", "image"], "output": ["text"]}
    config = build_vscode_config(
        app_base_url="http://127.0.0.1:8000",
        display_name="demo",
        api_key="sk",
        models=["gpt-4o", "deepseek-reasoner"],
        records=records,
    )
    by_id = {item["id"]: item for item in config["models"]}
    assert by_id["gpt-4o"]["maxInputTokens"] == 128000
    assert by_id["gpt-4o"]["maxOutputTokens"] == 16384
    assert by_id["gpt-4o"]["vision"] is True
    assert by_id["deepseek-reasoner"]["vision"] is False
