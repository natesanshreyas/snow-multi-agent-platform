"""
github_search_agent.py — Resolves Terraform module repos using Microsoft
Agent Framework with a real tool the agent calls itself.

The agent is wired with `tools=[search_module_repos]` (an `agent_framework.tool`
wrapper around mcp.github.search_module_repos). It then decides when and how
many times to invoke that tool to enumerate candidate repos for each unit
type, and finally returns a JSON mapping {unit_type: repo_name}.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from agent_framework import tool

from agents.client import get_model_client
from mcp.github import search_module_repos as _search_module_repos_raw

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a GitHub repository resolver for a Terraform automation platform.

You have one tool:
  - search_module_repos(module_type: str, org: str) -> list[str]
    Returns the candidate repos in the given org that contain
    modules/{module_type}/README.md.

For EACH unit_type provided in the user message, call search_module_repos
with that unit_type and the user-supplied org, then pick the best repo:

=== SELECTION RULES ===
1. If only one repo matches a type, use it.
2. If multiple repos match, prefer the one whose name most closely relates to
   the infrastructure domain (e.g. "terraform-azure-modules" for azure types).
3. If a repo name contains "prod" or "stable", prefer it over "dev" or "test".
4. If still ambiguous, pick the repo that appears most frequently across
   types (consistency — keep all modules in the same repo if possible).
5. If no repo matches a type, omit that type from the output.

=== OUTPUT FORMAT ===
Output ONLY a JSON object — no prose, no markdown:
{
  "unit_type": "repo_name",
  ...
}
"""


# Wrap the raw async function as an Agent Framework tool so the agent can
# call it autonomously. The original function remains usable directly by
# any non-agent caller in the workflow.
search_module_repos_tool = tool(
    _search_module_repos_raw,
    name="search_module_repos",
    description=(
        "Search a GitHub org for repos containing modules/{module_type}/README.md. "
        "Returns a list of candidate repo names, most relevant first. "
        "Empty list if nothing found."
    ),
)


def _parse_mapping(raw: str) -> Dict[str, str]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"GH Search Agent returned non-JSON: {raw[:300]}")


async def run_github_search_agent(
    unit_types: List[str],
    org: str,
) -> Dict[str, str]:
    """Resolve a Terraform module repo for each unit type via an MAF agent.

    The agent is constructed with a single `search_module_repos` tool; it
    issues one tool call per unit type and returns a JSON mapping.
    """
    if not unit_types:
        return {}

    client = get_model_client()
    agent = client.as_agent(
        name="github_search_agent",
        instructions=_SYSTEM_PROMPT,
        tools=[search_module_repos_tool],
    )

    user_content = (
        f"GitHub org: {org}\n"
        f"Resolve a repo for each of these Terraform unit types: "
        f"{json.dumps(unit_types)}.\n"
        f"Use the search_module_repos tool once per unit type, then output the "
        f"JSON mapping per the rules in your instructions."
    )

    result = await agent.run(user_content)
    raw = result.text
    logger.info("GH Search Agent output: %s", raw[:300])
    return _parse_mapping(raw)
