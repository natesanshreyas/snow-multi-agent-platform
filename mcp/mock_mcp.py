"""
mock_mcp.py — Stub implementations of all MCP tool calls for demo mode.

When DEMO_MODE=true (set automatically by demo_server.py), these replace
the real GitHub / ServiceNow / Azure MCP calls so the agents run end-to-end
without cloud credentials.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"


async def create_ticket_branch(ticket_id: str, org: str, repo: str) -> str:
    logger.info("[mock-mcp-github] create_branch: feature/%s in %s/%s", ticket_id, org, repo)
    await asyncio.sleep(0.2)
    return f"feature/{ticket_id}"


async def push_unit_terraform(
    branch: str, ticket_id: str, unit_id: str, environment: str,
    main_tf: str, variables_tf: str, org: str, repo: str,
) -> None:
    logger.info("[mock-mcp-github] push_unit_terraform: %s/%s/%s/main.tf", environment, ticket_id, unit_id)
    await asyncio.sleep(0.1)


async def create_pull_request(
    ticket_id: str, environment: str, org: str, repo: str,
    branch: str, unit_ids: List[str], description: str,
) -> str:
    logger.info("[mock-mcp-github] create_pull_request: %s → %s/%s", branch, org, repo)
    await asyncio.sleep(0.2)
    return f"https://github.com/{org}/{repo}/pulls"


async def search_module_repos(unit_type: str, org: str) -> List[str]:
    logger.info("[mock-mcp-github] search_code: modules/%s in org %s", unit_type, org)
    await asyncio.sleep(0.1)
    return [f"{org}/terraform-azure-modules"]


async def read_module_readme(unit_type: str, org: str, repo: str) -> str:
    logger.info("[mock-mcp-github] get_file_contents: modules/%s/README.md", unit_type)
    await asyncio.sleep(0.1)
    return f"# {unit_type} module\n\nRequired variables: name, resource_group_name, location, tags."


async def get_latest_module_version(unit_type: str, org: str, repo: str) -> str:
    await asyncio.sleep(0.05)
    return "abc1234"


async def scan_environment(resource_names: List[str], subscription_id: Optional[str]) -> Dict[str, Any]:
    logger.info("[mock-mcp-azure-resource-graph] query_resources: %s", resource_names)
    await asyncio.sleep(0.3)
    return {
        "existing_resource_groups": ["rg-payments-api-prod"],
        "existing_resources": [],
    }


async def write_questions_to_ticket(sys_id: str, ticket_id: str, run_id: str, questions: List[str]) -> None:
    logger.info("[mock-mcp-servicenow] update_work_notes: HITL 1 questions for %s", ticket_id)
    await asyncio.sleep(0.1)


async def write_cost_approval_to_ticket(
    sys_id: str, ticket_id: str, run_id: str,
    total_monthly_usd: float, unit_breakdown: List[Dict],
    quota_detail: str, quota_ok: bool,
) -> None:
    logger.info("[mock-mcp-servicenow] update_work_notes: HITL 2 cost approval for %s", ticket_id)
    await asyncio.sleep(0.1)


async def update_ticket_with_pr(sys_id: str, ticket_id: str, pr_url: str, summary: str) -> None:
    logger.info("[mock-mcp-servicenow] update_work_notes: PR complete for %s", ticket_id)
    await asyncio.sleep(0.1)
