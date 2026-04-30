from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Dict, List

from agents.client import get_model_client
from mcp.github import search_module_repos

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a GitHub repository resolver for a Terraform automation platform.

Given a list of infrastructure module types and the GitHub search results
showing which repos contain each module, select the best repo for each type.

=== SELECTION RULES ===
1. If only one repo matches a type, use it.
2. If multiple repos match, prefer the one whose name most closely relates to
   the infrastructure domain (e.g. "terraform-azure-modules" for azure types).
3. If a repo name contains "prod" or "stable", prefer it over "dev" or "test".
4. If still ambiguous, pick the repo that appears most frequently across types
   (consistency — keep all modules in the same repo if possible).
5. If no repo matches a type, omit that type from the output.

=== OUTPUT FORMAT ===
Output ONLY a JSON object — no prose, no markdown:
{
  "unit_type": "repo_name",
  ...
}
"""


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
    search_results: Dict[str, List[str]] = {}
    results = await asyncio.gather(
        *[search_module_repos(t, org) for t in unit_types],
        return_exceptions=True,
    )
    for unit_type, result in zip(unit_types, results):
        if isinstance(result, Exception):
            logger.warning("search failed for %s: %s", unit_type, result)
            search_results[unit_type] = []
        else:
            search_results[unit_type] = result

    mapping: Dict[str, str] = {}
    ambiguous: Dict[str, List[str]] = {}
    for t, repos in search_results.items():
        if len(repos) == 1:
            mapping[t] = repos[0]
        elif len(repos) > 1:
            ambiguous[t] = repos

    if not ambiguous:
        logger.info("GH Search Agent: all types resolved without LLM — %s", mapping)
        return mapping

    client = get_model_client()
    agent = client.as_agent(
        name="github_search_agent",
        instructions=_SYSTEM_PROMPT,
    )

    search_summary = json.dumps(search_results, indent=2)
    user_content = (
        f"GitHub org: {org}\n\n"
        f"Search results (unit_type → candidate repos):\n{search_summary}\n\n"
        f"Select the best repo for each type and return the JSON mapping."
    )

    result = await agent.run(user_content)
    raw = result.text
    logger.info("GH Search Agent output: %s", raw[:300])

    llm_mapping = _parse_mapping(raw)
    mapping.update(llm_mapping)
    return mapping
