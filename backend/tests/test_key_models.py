from __future__ import annotations

import json

import pytest

from app.services.key_models import (
    PREFIX_ERROR,
    build_model_catalog,
    default_model_prefix,
    normalize_model_prefix,
    resolve_model,
    slug_model_prefix,
)


class FakeAccount:
    def __init__(
        self,
        account_id: int,
        name: str,
        *,
        status: str = "active",
        models: list[str] | None = None,
        prefix: str | None = None,
        source: str = "upstream",
        agent_route_id: str | None = None,
    ) -> None:
        self.id = account_id
        self.name = name
        self.status = status
        self.models_json = json.dumps(models) if models is not None else None
        self.model_prefix = prefix
        self.source = source
        self.agent_route_id = agent_route_id


class FakeLink:
    def __init__(self, sort_order: int, account: FakeAccount) -> None:
        self.sort_order = sort_order
        self.account = account


class FakeKey:
    def __init__(self, accounts: list[FakeAccount]) -> None:
        self.account_links = [FakeLink(index, account) for index, account in enumerate(accounts)]
        self.account = accounts[0] if accounts else None


def test_slug_and_default_prefix() -> None:
    assert slug_model_prefix("DeepSeek Main") == "DeepSeek-Main"
    assert slug_model_prefix("DS") == "DS"
    assert slug_model_prefix("主号") == ""
    assert default_model_prefix("主号", 7) == "acc-7"
    assert default_model_prefix("DeepSeek 主号", 7) == "DeepSeek"


def test_normalize_model_prefix_rejects_illegal_values() -> None:
    assert normalize_model_prefix(None) is None
    assert normalize_model_prefix("  ") is None
    assert normalize_model_prefix("ds_main") == "ds_main"
    with pytest.raises(ValueError, match=PREFIX_ERROR):
        normalize_model_prefix("ds/main")
    with pytest.raises(ValueError, match=PREFIX_ERROR):
        normalize_model_prefix("-ds")
    with pytest.raises(ValueError, match=PREFIX_ERROR):
        normalize_model_prefix("x" * 33)


def test_catalog_keeps_unique_raw_ids() -> None:
    key = FakeKey(
        [
            FakeAccount(1, "one", models=["chat", "coder"], prefix="one"),
            FakeAccount(2, "two", models=["reasoner"], prefix="two"),
        ]
    )
    catalog = build_model_catalog(key)
    assert [entry.public_id for entry in catalog] == ["chat", "coder", "reasoner"]
    assert all(entry.public_id == entry.raw_id for entry in catalog)


def test_catalog_deduplicates_models_from_one_account() -> None:
    key = FakeKey([FakeAccount(1, "one", models=["chat", "chat", " coder ", "coder"], prefix="one")])

    assert [entry.public_id for entry in build_model_catalog(key)] == ["chat", "coder"]


def test_catalog_prefixes_colliding_models() -> None:
    key = FakeKey(
        [
            FakeAccount(1, "one", models=["shared", "only-a"], prefix="one"),
            FakeAccount(2, "two", models=["shared", "only-b"], prefix="two"),
        ]
    )
    catalog = {entry.public_id: entry for entry in build_model_catalog(key)}
    assert catalog["shared"].account.id == 1
    assert catalog["shared"].raw_id == "shared"
    assert catalog["two/shared"].account.id == 2
    assert catalog["two/shared"].raw_id == "shared"
    assert catalog["only-a"].account.id == 1
    assert catalog["only-b"].account.id == 2


def test_catalog_uses_prefix_and_account_id_on_double_collision() -> None:
    key = FakeKey(
        [
            FakeAccount(1, "one", models=["chat"], prefix="ds"),
            FakeAccount(2, "two", models=["chat"], prefix="ds"),
            FakeAccount(3, "three", models=["chat"], prefix="ds"),
        ]
    )
    assert [entry.public_id for entry in build_model_catalog(key)] == ["chat", "ds/chat", "ds-3/chat"]


def test_catalog_skips_disabled_accounts() -> None:
    key = FakeKey(
        [
            FakeAccount(1, "one", status="disabled", models=["chat"], prefix="one"),
            FakeAccount(2, "two", models=["chat"], prefix="two"),
        ]
    )
    catalog = build_model_catalog(key)
    assert [entry.public_id for entry in catalog] == ["chat"]
    assert catalog[0].account.id == 2


def test_catalog_skips_offline_agent_accounts(monkeypatch) -> None:
    account = FakeAccount(1, "local", models=["chat"], prefix="local", source="agent", agent_route_id="local-route")
    key = FakeKey([account])
    monkeypatch.setattr("app.services.local_agent_relay.local_agent_relay.is_agent_online_for_route", lambda route_id: False)

    assert build_model_catalog(key) == []


def test_catalog_excludes_empty_models_when_multiple_accounts() -> None:
    key = FakeKey(
        [
            FakeAccount(1, "one", models=["chat"], prefix="one"),
            FakeAccount(2, "two", models=[], prefix="two"),
        ]
    )
    assert [entry.public_id for entry in build_model_catalog(key)] == ["chat"]


def test_single_account_empty_models_passthrough() -> None:
    account = FakeAccount(1, "one", models=[], prefix="one")
    key = FakeKey([account])
    catalog = build_model_catalog(key)
    assert catalog == []
    entry = resolve_model(catalog, "any-model", single_account=account)
    assert entry is not None
    assert entry.raw_id == "any-model"
    assert entry.account.id == 1
