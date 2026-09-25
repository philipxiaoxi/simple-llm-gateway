from __future__ import annotations

from app.capabilities.base import CapabilitySpec, McpToolDef, Provider

_providers: dict[str, Provider] = {}
# tool_name -> (capability_id, operation)
_tool_index: dict[str, tuple[str, str]] = {}


def register(provider: Provider) -> None:
    capability_id = provider.spec.capability_id
    _providers[capability_id] = provider
    for tool in provider.list_mcp_tools():
        if tool.name in _tool_index and _tool_index[tool.name][0] != capability_id:
            raise ValueError(f"MCP tool 名冲突: {tool.name}")
        _tool_index[tool.name] = (capability_id, tool.resolved_operation())


def get_provider(capability_id: str) -> Provider | None:
    return _providers.get(capability_id)


def list_providers(*, only_enabled: bool = True) -> list[Provider]:
    items = list(_providers.values())
    if only_enabled:
        items = [item for item in items if item.spec.status == "enabled"]
    return sorted(items, key=lambda item: item.spec.capability_id)


def list_specs(*, only_enabled: bool = True) -> list[CapabilitySpec]:
    return [provider.spec for provider in list_providers(only_enabled=only_enabled)]


def resolve_tool(tool_name: str) -> tuple[str, str] | None:
    return _tool_index.get(tool_name)


def list_tool_defs(*, only_enabled: bool = True) -> list[tuple[str, McpToolDef]]:
    result: list[tuple[str, McpToolDef]] = []
    for provider in list_providers(only_enabled=only_enabled):
        for tool in provider.list_mcp_tools():
            result.append((provider.spec.capability_id, tool))
    return result


def catalog_payload(*, only_enabled: bool = True) -> list[dict]:
    rows = []
    for provider in list_providers(only_enabled=only_enabled):
        spec = provider.spec
        tools = provider.list_mcp_tools()
        rows.append(
            {
                "capability_id": spec.capability_id,
                "name": spec.name,
                "description": spec.description,
                "version": spec.version,
                "category": spec.category,
                "status": spec.status,
                "admin_path": spec.admin_path,
                "icon": spec.icon,
                "input_schema": spec.input_schema,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "operation": tool.resolved_operation(),
                        "input_schema": tool.input_schema,
                    }
                    for tool in tools
                ],
            }
        )
    return rows


def ensure_defaults() -> None:
    """注册内置应用。后续新应用在此追加一行 register(XxxProvider()) 即可。"""
    if "knowledge" not in _providers:
        from app.capabilities.knowledge.provider import KnowledgeProvider

        register(KnowledgeProvider())
    if "docparse" not in _providers:
        from app.capabilities.docparse.provider import DocParseProvider

        register(DocParseProvider())


def reset_registry_for_tests() -> None:
    _providers.clear()
    _tool_index.clear()
