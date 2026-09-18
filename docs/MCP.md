# Research Radar MCP Server

Research Radar ships a [Model Context Protocol](https://modelcontextprotocol.io) server so Claude, Cursor, Codex, DSH agents and other MCP clients can operate on the same local data.

## Run

```bash
python -m radar.mcp_server
# or after pip install -e .
radar-mcp
```

The server uses the default local database (`SessionLocal`), same as the web app. For a custom database, set `DATABASE_URL` before launching.

## Configure in an MCP client

Example Claude Desktop / Cursor / DSH MCP config:

```json
{
  "mcpServers": {
    "research-radar": {
      "command": "python",
      "args": ["-m", "radar.mcp_server"]
    }
  }
}
```

## Exposed tools

| Tool | Description |
|------|-------------|
| `list_cases` | List all research cases |
| `get_case` | Get one case's metadata |
| `list_sources` | List case-referenced literature sources and snapshot counts |
| `cost_by_source` | Return LLM cost grouped by source kind |
| `remove_sources` | Remove source references from a case without deleting global sources |
| `get_audit` | Return the audit trail for a case |
| `get_retrieval_receipts` | Return retrieval receipts for a case |
| `get_citation_health` | Return citation health for a case |
| `get_source_detail` | Get one case-referenced source and its related evidence |
| `list_scans` | List scan/deep-research runs |
| `get_scan_status` | Get one run's status/stats |
| `get_monitoring_stats` | Get monitoring health stats (scan counts / failures / last scan) |
| `start_scan` | Start a claim-driven literature radar scan |
| `start_deep_research_tool` | Start a bounded deep-research run |
| `get_auto_scan_config` | Get a case's auto-scan configuration |
| `set_auto_scan_config` | Enable/disable periodic radar scans |
| `get_scan_webhook` | Get scan completion webhook configuration |
| `set_scan_webhook` | Enable/disable scan completion webhook notifications |
| `get_scan_filters` | Get scan filter preferences (CCF rank / arXiv categories) |
| `set_scan_filters` | Persist scan filter preferences |
| `get_cost_alert` | Get monthly cost-alert configuration |
| `set_cost_alert` | Enable/disable monthly cost alert |
| `test_cost_alert` | Send a one-off cost-alert webhook for testing |
| `get_digest_webhook_config` | Get scheduled digest webhook configuration |
| `set_digest_webhook_config` | Enable/disable scheduled digest webhook sending |
| `test_digest_webhook` | Send a one-off digest to the configured webhook for testing |
| `execute_skill` | Execute a CCF-A skill (writer/reviewer/auditor/experiment/rebuttal/reference auditor) |
| `run_skill_pipeline` | Run a sequence of CCF-A skills (revision/review/experiment) |
| `list_skill_runs` | List recent CCF-A skill execution records |
| `list_skill_pipeline_runs` | List recent CCF-A skill pipeline execution records |
| `list_notifications` | List recent scheduler notification events (webhook sent/failed, auto-scan failures) |
| `import_source` | Import one external paper (arXiv URL / DOI) into a case |
| `list_impacts` | List impact-candidate papers with evidence/stance |
| `list_actions` | List active action items |
| `generate_digest` | Generate a personalized weekly radar digest (Markdown + structured) |
| `ask_literature` | Ask a question over collected papers |

## Notes

- All tools operate on the local-first database and never modify the user's manuscript automatically.
- `start_scan` / `start_deep_research_tool` return a `scan_id`; poll `get_scan_status` to wait for completion.
- The MCP server is a thin adapter over the same services the web app uses, so trust gates (G0/G1/G2) still apply.
