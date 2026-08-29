from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.clock import utcnow
from app.config import get_settings

MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_TTL = timedelta(hours=48)
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
DEFAULT_INPUT_MODALITIES = ("text",)
DEFAULT_OUTPUT_MODALITIES = ("text",)

PROVIDER_CATALOG_IDS = {
    "deepseek": "deepseek",
    "grok": "xai",
    "openai_generic": "openai",
    "anthropic_generic": "anthropic",
}

REASONING_MARKERS = (
    "reasoner",
    "reasoning",
    "thinking",
    "-r1",
    "r1-",
    "o1-",
    "-o1",
    "o3-",
    "-o3",
    "o4-mini",
    "grok-4",
)
VISION_MARKERS = ("vision", "gpt-4o", "gpt-4.1", "claude-3", "gemini-")
_CACHE: tuple[datetime, "CatalogIndex"] | None = None


@dataclass(frozen=True)
class ModelCaps:
    context_window: int | None = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS
    reasoning: bool = False
    reasoning_efforts: tuple[str, ...] | None = None
    input_modalities: tuple[str, ...] = DEFAULT_INPUT_MODALITIES
    output_modalities: tuple[str, ...] = DEFAULT_OUTPUT_MODALITIES
    source: str = "heuristic"


@dataclass
class ModelRecord:
    id: str
    auto: ModelCaps
    overrides: dict[str, Any] = field(default_factory=dict)

    def effective(self) -> ModelCaps:
        caps = self.auto
        overrides = self.overrides
        if not overrides:
            return caps
        efforts = overrides.get("reasoning_efforts", _MISSING)
        modalities = overrides.get("modalities")
        input_modalities = caps.input_modalities
        output_modalities = caps.output_modalities
        if isinstance(modalities, dict):
            input_modalities = _string_tuple(modalities.get("input"), input_modalities)
            output_modalities = _string_tuple(modalities.get("output"), output_modalities)
        return replace(
            caps,
            context_window=_override_int(overrides, "context_window", caps.context_window),
            max_output_tokens=_override_int(overrides, "max_output_tokens", caps.max_output_tokens),
            reasoning=_override_bool(overrides, "reasoning", caps.reasoning),
            reasoning_efforts=_string_tuple(efforts, None) if efforts is not _MISSING else caps.reasoning_efforts,
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            source="override" if overrides else caps.source,
        )


@dataclass
class CatalogIndex:
    by_provider_id: dict[tuple[str, str], ModelCaps] = field(default_factory=dict)
    by_provider_norm: dict[tuple[str, str], ModelCaps] = field(default_factory=dict)
    by_norm: dict[str, ModelCaps] = field(default_factory=dict)


_MISSING = object()


def normalize_model_id(model_id: str) -> str:
    value = (model_id or "").strip().lower()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value.replace(".", "-").replace("_", "-")


def parse_model_records(raw: str | None) -> list[ModelRecord]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    records: list[ModelRecord] = []
    seen: set[str] = set()
    for item in parsed:
        record = _record_from_stored(item)
        if record is None or record.id in seen:
            continue
        seen.add(record.id)
        records.append(record)
    return records


def parse_models_json(raw: str | None) -> list[str]:
    return [record.id for record in parse_model_records(raw)]


def dump_model_records(records: list[ModelRecord]) -> str:
    return json.dumps([record_to_stored(record) for record in records], ensure_ascii=False)


def record_to_stored(record: ModelRecord) -> dict[str, Any]:
    caps = record.auto
    payload: dict[str, Any] = {
        "id": record.id,
        "context_window": caps.context_window,
        "max_output_tokens": caps.max_output_tokens,
        "reasoning": caps.reasoning,
        "reasoning_efforts": list(caps.reasoning_efforts) if caps.reasoning_efforts else None,
        "modalities": {
            "input": list(caps.input_modalities),
            "output": list(caps.output_modalities),
        },
        "source": caps.source,
    }
    if record.overrides:
        payload["overrides"] = record.overrides
    return payload


def serialize_record(record: ModelRecord) -> dict[str, Any]:
    caps = record.effective()
    return {
        "id": record.id,
        "context_window": caps.context_window,
        "max_output_tokens": caps.max_output_tokens,
        "reasoning": caps.reasoning,
        "reasoning_efforts": list(caps.reasoning_efforts) if caps.reasoning_efforts else None,
        "modalities": {
            "input": list(caps.input_modalities),
            "output": list(caps.output_modalities),
        },
        "source": record.auto.source,
        "overridden": sorted(record.overrides),
        "overrides": dict(record.overrides),
    }


def first_model_id(raw: str | None) -> str:
    models = parse_models_json(raw)
    return models[0] if models else ""


def extract_model_entries(payload: object) -> list[dict[str, Any]]:
    entries: list[Any] = []
    if isinstance(payload, dict):
        raw_entries = payload.get("data") or payload.get("models") or payload.get("items") or []
        if isinstance(raw_entries, list):
            entries = raw_entries
    elif isinstance(payload, list):
        entries = payload
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        model_id = ""
        raw: dict[str, Any] | None = None
        if isinstance(entry, str):
            model_id = entry.strip()
        elif isinstance(entry, dict):
            raw = entry
            model_id = str(entry.get("id") or entry.get("name") or entry.get("model") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        item: dict[str, Any] = {"id": model_id}
        if raw is not None:
            item["entry"] = raw
        result.append(item)
    return result


def extract_model_ids(payload: object) -> list[str]:
    return [item["id"] for item in extract_model_entries(payload)]


def caps_from_heuristic(model_id: str) -> ModelCaps:
    normalized = normalize_model_id(model_id)
    reasoning = any(marker in normalized for marker in REASONING_MARKERS)
    vision = any(marker in normalized for marker in VISION_MARKERS)
    input_modalities = ("text", "image") if vision else DEFAULT_INPUT_MODALITIES
    return ModelCaps(
        context_window=DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        reasoning=reasoning,
        reasoning_efforts=None,
        input_modalities=input_modalities,
        output_modalities=DEFAULT_OUTPUT_MODALITIES,
        source="heuristic",
    )


def caps_from_upstream_entry(entry: dict[str, Any] | None) -> ModelCaps | None:
    if not entry:
        return None
    context = _first_int(
        entry.get("context_window"),
        entry.get("context_length"),
        (entry.get("top_provider") or {}).get("context_length") if isinstance(entry.get("top_provider"), dict) else None,
        (entry.get("limit") or {}).get("context") if isinstance(entry.get("limit"), dict) else None,
    )
    output = _first_int(
        entry.get("max_output_tokens"),
        entry.get("max_tokens"),
        (entry.get("top_provider") or {}).get("max_completion_tokens") if isinstance(entry.get("top_provider"), dict) else None,
        (entry.get("limit") or {}).get("output") if isinstance(entry.get("limit"), dict) else None,
    )
    reasoning = _upstream_reasoning(entry)
    efforts = _effort_values(entry.get("reasoning_options"))
    input_modalities, output_modalities = _modalities_from_entry(entry)
    if context is None and output is None and reasoning is None and efforts is None and input_modalities is None:
        return None
    heuristic = caps_from_heuristic(str(entry.get("id") or ""))
    return ModelCaps(
        context_window=context if context is not None else heuristic.context_window,
        max_output_tokens=output if output is not None else heuristic.max_output_tokens,
        reasoning=heuristic.reasoning if reasoning is None else reasoning,
        reasoning_efforts=efforts,
        input_modalities=input_modalities or heuristic.input_modalities,
        output_modalities=output_modalities or heuristic.output_modalities,
        source="upstream",
    )


def caps_from_catalog_entry(entry: dict[str, Any]) -> ModelCaps:
    limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else {}
    context = _first_int(limit.get("context"), entry.get("context_window"), entry.get("context_length"))
    output = _first_int(limit.get("output"), entry.get("max_output_tokens"), entry.get("max_tokens"))
    modalities = entry.get("modalities") if isinstance(entry.get("modalities"), dict) else {}
    input_modalities = _string_tuple(modalities.get("input"), DEFAULT_INPUT_MODALITIES)
    output_modalities = _string_tuple(modalities.get("output"), DEFAULT_OUTPUT_MODALITIES)
    reasoning = bool(entry.get("reasoning"))
    efforts = _effort_values(entry.get("reasoning_options"))
    return ModelCaps(
        context_window=context if context is not None else DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=output if output is not None else DEFAULT_MAX_OUTPUT_TOKENS,
        reasoning=reasoning,
        reasoning_efforts=efforts if reasoning else None,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        source="catalog",
    )


def build_catalog_index(payload: dict[str, Any]) -> CatalogIndex:
    grouped: dict[str, list[ModelCaps]] = {}
    index = CatalogIndex()
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, entry in models.items():
            if not isinstance(entry, dict):
                continue
            raw_id = str(entry.get("id") or model_id)
            caps = caps_from_catalog_entry(entry)
            index.by_provider_id[(provider_id, raw_id)] = caps
            index.by_provider_norm[(provider_id, normalize_model_id(raw_id))] = caps
            grouped.setdefault(normalize_model_id(raw_id), []).append(caps)
    index.by_norm = {key: _majority_caps(values) for key, values in grouped.items()}
    return index


def match_catalog(model_id: str, provider: str | None, index: CatalogIndex) -> ModelCaps | None:
    catalog_provider = PROVIDER_CATALOG_IDS.get(provider or "")
    if catalog_provider:
        exact = index.by_provider_id.get((catalog_provider, model_id))
        if exact is not None:
            return exact
        normalized = index.by_provider_norm.get((catalog_provider, normalize_model_id(model_id)))
        if normalized is not None:
            return normalized
    return index.by_norm.get(normalize_model_id(model_id))


def resolve_auto_caps(
    model_id: str,
    *,
    provider: str | None = None,
    upstream: ModelCaps | None = None,
    catalog: CatalogIndex | None = None,
) -> ModelCaps:
    if catalog is not None:
        matched = match_catalog(model_id, provider, catalog)
        if matched is not None:
            return matched
    heuristic = caps_from_heuristic(model_id)
    if upstream is None:
        return heuristic
    return ModelCaps(
        context_window=upstream.context_window if upstream.context_window is not None else heuristic.context_window,
        max_output_tokens=upstream.max_output_tokens if upstream.max_output_tokens is not None else heuristic.max_output_tokens,
        reasoning=upstream.reasoning,
        reasoning_efforts=upstream.reasoning_efforts,
        input_modalities=upstream.input_modalities or heuristic.input_modalities,
        output_modalities=upstream.output_modalities or heuristic.output_modalities,
        source=upstream.source,
    )


def enrich_model_records(
    entries: list[dict[str, Any]],
    existing_raw: str | None,
    *,
    provider: str | None = None,
    catalog: CatalogIndex | None = None,
) -> list[ModelRecord]:
    existing = {record.id: record for record in parse_model_records(existing_raw)}
    records: list[ModelRecord] = []
    for entry in entries:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        upstream = caps_from_upstream_entry(entry.get("entry") if isinstance(entry.get("entry"), dict) else None)
        auto = resolve_auto_caps(model_id, provider=provider, upstream=upstream, catalog=catalog)
        previous = existing.get(model_id)
        records.append(ModelRecord(id=model_id, auto=auto, overrides=dict(previous.overrides) if previous else {}))
    return records


def apply_model_override(records: list[ModelRecord], model_id: str, payload: dict[str, Any]) -> list[ModelRecord]:
    updated: list[ModelRecord] = []
    found = False
    for record in records:
        if record.id != model_id:
            updated.append(record)
            continue
        found = True
        if payload.get("clear"):
            updated.append(ModelRecord(id=record.id, auto=record.auto, overrides={}))
            continue
        overrides = dict(record.overrides)
        for key in ("context_window", "max_output_tokens", "reasoning", "reasoning_efforts", "modalities"):
            if key in payload:
                value = payload[key]
                if value is None:
                    overrides.pop(key, None)
                else:
                    overrides[key] = value
        updated.append(ModelRecord(id=record.id, auto=record.auto, overrides=overrides))
    if not found:
        raise ValueError("模型不存在")
    return updated


def catalog_cache_path() -> Path:
    settings = get_settings()
    if settings.database_path == ":memory:":
        return Path("data") / "models_dev_cache.json"
    return Path(settings.database_path).expanduser().resolve().parent / "models_dev_cache.json"


def load_catalog_index(*, force: bool = False) -> CatalogIndex:
    global _CACHE
    now = utcnow()
    if not force and _CACHE is not None and now - _CACHE[0] < MODELS_DEV_TTL:
        return _CACHE[1]
    path = catalog_cache_path()
    cached_payload, cached_at = _read_cache_file(path)
    if not force and cached_payload is not None and cached_at is not None and now - cached_at < MODELS_DEV_TTL:
        index = build_catalog_index(cached_payload)
        _CACHE = (cached_at, index)
        return index
    fetched = _fetch_models_dev()
    if fetched is not None:
        _write_cache_file(path, fetched, now)
        index = build_catalog_index(fetched)
        _CACHE = (now, index)
        return index
    if cached_payload is not None:
        index = build_catalog_index(cached_payload)
        _CACHE = (cached_at or now, index)
        return index
    empty = CatalogIndex()
    _CACHE = (now, empty)
    return empty


def reset_catalog_cache() -> None:
    global _CACHE
    _CACHE = None


def _record_from_stored(item: Any) -> ModelRecord | None:
    if isinstance(item, str):
        model_id = item.strip()
        if not model_id:
            return None
        return ModelRecord(id=model_id, auto=caps_from_heuristic(model_id))
    if not isinstance(item, dict):
        return None
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None
    modalities = item.get("modalities") if isinstance(item.get("modalities"), dict) else {}
    auto = ModelCaps(
        context_window=_as_int(item.get("context_window")) or DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=_as_int(item.get("max_output_tokens")) or DEFAULT_MAX_OUTPUT_TOKENS,
        reasoning=bool(item.get("reasoning")),
        reasoning_efforts=_string_tuple(item.get("reasoning_efforts"), None),
        input_modalities=_string_tuple(modalities.get("input"), DEFAULT_INPUT_MODALITIES),
        output_modalities=_string_tuple(modalities.get("output"), DEFAULT_OUTPUT_MODALITIES),
        source=str(item.get("source") or "heuristic"),
    )
    overrides = item.get("overrides") if isinstance(item.get("overrides"), dict) else {}
    return ModelRecord(id=model_id, auto=auto, overrides=dict(overrides))


def _effort_values(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, list):
        return None
    values: list[str] = []
    for option in raw:
        if not isinstance(option, dict):
            continue
        if str(option.get("type") or "") != "effort":
            continue
        for value in option.get("values") or []:
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
    return tuple(values) if values else None


def _modalities_from_entry(entry: dict[str, Any]) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    modalities = entry.get("modalities")
    if isinstance(modalities, dict):
        return (
            _string_tuple(modalities.get("input"), None),
            _string_tuple(modalities.get("output"), None),
        )
    architecture = entry.get("architecture")
    if isinstance(architecture, dict):
        inputs = architecture.get("input_modalities") or architecture.get("modality")
        outputs = architecture.get("output_modalities")
        if isinstance(inputs, str):
            parts = tuple(part.strip() for part in re.split(r"[+,]", inputs) if part.strip())
            return (parts or None, _string_tuple(outputs, None))
        return (_string_tuple(inputs, None), _string_tuple(outputs, None))
    return (None, None)


def _upstream_reasoning(entry: dict[str, Any]) -> bool | None:
    if "reasoning" in entry:
        return bool(entry.get("reasoning"))
    supported = entry.get("supported_parameters")
    if isinstance(supported, list) and any(str(item).lower() in {"reasoning", "reasoning_effort", "thinking"} for item in supported):
        return True
    return None


def _majority_caps(values: list[ModelCaps]) -> ModelCaps:
    return ModelCaps(
        context_window=_majority_int([item.context_window for item in values if item.context_window]),
        max_output_tokens=_majority_int([item.max_output_tokens for item in values if item.max_output_tokens]),
        reasoning=_majority_bool([item.reasoning for item in values]),
        reasoning_efforts=_majority_tuple([item.reasoning_efforts for item in values if item.reasoning_efforts]),
        input_modalities=_majority_tuple([item.input_modalities for item in values]) or DEFAULT_INPUT_MODALITIES,
        output_modalities=_majority_tuple([item.output_modalities for item in values]) or DEFAULT_OUTPUT_MODALITIES,
        source="catalog",
    )


def _majority_int(values: list[int]) -> int | None:
    if not values:
        return None
    counts = Counter(values)
    best = counts.most_common(1)[0][1]
    tied = [value for value, count in counts.items() if count == best]
    return max(tied)


def _majority_bool(values: list[bool]) -> bool:
    if not values:
        return False
    return Counter(values).most_common(1)[0][0]


def _majority_tuple(values: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _string_tuple(value: Any, default: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else default
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
        return tuple(items) if items else default
    return default


def _override_int(overrides: dict[str, Any], key: str, current: int | None) -> int | None:
    if key not in overrides:
        return current
    parsed = _as_int(overrides[key])
    return parsed if parsed is not None else current


def _override_bool(overrides: dict[str, Any], key: str, current: bool) -> bool:
    if key not in overrides:
        return current
    value = overrides[key]
    if isinstance(value, bool):
        return value
    return current


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _fetch_models_dev() -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": "pivot-desk/model-caps"}) as client:
            response = client.get(MODELS_DEV_URL)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_cache_file(path: Path) -> tuple[dict[str, Any] | None, datetime | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(raw, dict):
        return None, None
    payload = raw.get("payload")
    fetched_at = raw.get("fetched_at")
    parsed_at = None
    if isinstance(fetched_at, str):
        try:
            parsed_at = datetime.fromisoformat(fetched_at)
        except ValueError:
            parsed_at = None
    return (payload if isinstance(payload, dict) else None, parsed_at)


def _write_cache_file(path: Path, payload: dict[str, Any], fetched_at: datetime) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": fetched_at.isoformat(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return
