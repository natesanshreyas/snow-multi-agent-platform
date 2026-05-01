"""
mock_client.py - Drop-in mock for the Microsoft Agent Framework chat client.

Returns hardcoded realistic responses so the agents (`run_planner_agent`,
`run_github_search_agent`, `run_terraform_agent`) execute fully without any
LLM credentials.

The real client (agents/client.py -> AzureOpenAIChatClient) is consumed via:

    client = get_model_client()
    agent  = client.as_agent(name=..., instructions=...)
    result = await agent.run(user_content)
    text   = result.text

This mock implements the same surface (`.as_agent()` -> object with async
`.run()` returning something with `.text`), so callers don't need to know.

The mock detects which agent is calling by inspecting the `instructions`
string passed to `as_agent()`, then returns the matching JSON response.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_PLANNER_INITIAL = json.dumps({
    "units": [
        {"id": "app_rg", "type": "resource_group", "depends_on": [],
         "constraints": {"required_rg": None, "forbidden_rg": None, "location": "eastus2"}},
        {"id": "postgres_flex", "type": "postgres_flex", "depends_on": ["app_rg"],
         "constraints": {"required_rg": "rg-payments-api-prod",
                         "forbidden_rg": "app_rg", "location": "eastus2"}},
        {"id": "storage_account", "type": "storage_account", "depends_on": ["app_rg"],
         "constraints": {"required_rg": "rg-payments-api-prod",
                         "forbidden_rg": None, "location": "eastus2"}},
    ],
    "questions": [
        "Resource group **rg-payments-api-prod** already exists in the subscription. "
        "Should the Terraform use this existing resource group, or create a new one?"
    ],
})

_PLANNER_FINAL = json.dumps({
    "units": [
        {"id": "app_rg", "type": "resource_group", "depends_on": [],
         "constraints": {"required_rg": None, "forbidden_rg": None, "location": "eastus2"}},
        {"id": "postgres_flex", "type": "postgres_flex", "depends_on": ["app_rg"],
         "constraints": {"required_rg": "rg-payments-api-prod",
                         "forbidden_rg": "app_rg", "location": "eastus2"}},
        {"id": "storage_account", "type": "storage_account", "depends_on": ["app_rg"],
         "constraints": {"required_rg": "rg-payments-api-prod",
                         "forbidden_rg": None, "location": "eastus2"}},
    ],
    "questions": [],
}) + "\nPLAN_FINALIZED"

_GH_SEARCH = json.dumps({
    "resource_group":  "natesanshreyas/terraform-azure-modules",
    "postgres_flex":   "natesanshreyas/terraform-azure-modules",
    "storage_account": "natesanshreyas/terraform-azure-modules",
    "vpc":             "natesanshreyas/terraform-aws-modules",
    "rds_postgres":    "natesanshreyas/terraform-aws-modules",
    "s3_bucket":       "natesanshreyas/terraform-aws-modules",
    "sf_database":     "natesanshreyas/terraform-snowflake-modules",
    "sf_schema":       "natesanshreyas/terraform-snowflake-modules",
    "sf_warehouse":    "natesanshreyas/terraform-snowflake-modules",
})

_TF_OUTPUT = json.dumps({
    "main_tf": (
        'module "{unit_id}" {{\n'
        '  source = "git::https://github.com/natesanshreyas/terraform-azure-modules.git'
        '//modules/{unit_type}?ref=abc1234"\n\n'
        '  resource_group_name = var.resource_group_name\n'
        '  location            = var.location\n'
        '  name                = "{unit_id}"\n\n'
        '  tags = {{\n'
        '    environment = var.environment\n'
        '    ticket_id   = var.ticket_id\n'
        '    cost_center = var.cost_center\n'
        '  }}\n'
        '}}'
    ),
    "variables_tf": (
        'variable "resource_group_name" {{ type = string }}\n'
        'variable "location"            {{ type = string  default = "eastus2" }}\n'
        'variable "environment"         {{ type = string }}\n'
        'variable "ticket_id"           {{ type = string }}\n'
        'variable "cost_center"         {{ type = string  default = "fin-ops" }}\n'
    ),
})


def _classify(instructions: str, user_content: str) -> str:
    instr = (instructions or "").lower()
    user = (user_content or "").lower()
    if "infrastructure planner" in instr:
        if "incorporate these" in user:
            return "planner_final"
        return "planner_initial"
    if "terraform code generator" in instr:
        return "tf_generator"
    if "repository resolver" in instr:
        return "gh_search"
    return "unknown"


@dataclass
class _MockAgentRunResult:
    text: str


class _MockAgent:
    """Mimics what AzureOpenAIChatClient.as_agent(...) returns."""

    def __init__(self, name: str, instructions: str) -> None:
        self.name = name
        self.instructions = instructions

    async def run(self, user_content: str) -> _MockAgentRunResult:
        kind = _classify(self.instructions, user_content)
        logger.info("MockAgent name=%s classified as=%s", self.name, kind)
        if kind == "planner_initial":
            return _MockAgentRunResult(_PLANNER_INITIAL)
        if kind == "planner_final":
            return _MockAgentRunResult(_PLANNER_FINAL)
        if kind == "gh_search":
            return _MockAgentRunResult(_GH_SEARCH)
        if kind == "tf_generator":
            return _MockAgentRunResult(_TF_OUTPUT)
        return _MockAgentRunResult("{}")


class MockModelClient:
    """Drop-in mock for `agent_framework.azure.AzureOpenAIChatClient`.

    Only the slice the agents actually use is implemented:
        client.as_agent(name=..., instructions=...) -> object
        agent.run(user_content)                     -> object with .text
    """

    def as_agent(self, name: str, instructions: str = "", **_: object) -> _MockAgent:
        return _MockAgent(name=name, instructions=instructions)

    async def close(self) -> None:
        return None
