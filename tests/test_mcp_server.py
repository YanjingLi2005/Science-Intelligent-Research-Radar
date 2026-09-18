"""MCP server registration and tool surface."""

from mcp.server.fastmcp import FastMCP


def test_mcp_server_registers_core_tools():
    import radar.mcp_server as mcp_module

    assert isinstance(mcp_module.mcp, FastMCP)
    names = {tool.name for tool in mcp_module.mcp._tool_manager.list_tools()}
    assert {
        "list_cases",
        "get_case",
        "list_sources",
        "cost_by_source",
        "get_source_detail",
        "remove_sources",
        "get_audit",
        "get_retrieval_receipts",
        "get_citation_health",
        "list_scans",
        "get_scan_status",
        "get_monitoring_stats",
        "start_scan",
        "start_deep_research_tool",
        "get_auto_scan_config",
        "set_auto_scan_config",
        "get_scan_webhook",
        "set_scan_webhook",
        "get_scan_filters",
        "set_scan_filters",
        "get_cost_alert",
        "set_cost_alert",
        "test_cost_alert",
        "get_digest_webhook_config",
        "set_digest_webhook_config",
        "test_digest_webhook",
        "execute_skill",
        "run_skill_pipeline",
        "list_skill_runs",
        "list_skill_pipeline_runs",
        "list_notifications",
        "import_source",
        "list_impacts",
        "list_actions",
        "generate_digest",
        "ask_literature",
    } <= names
