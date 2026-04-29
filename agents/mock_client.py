"""
mock_client.py — Drop-in mock for AzureOpenAIChatCompletionClient.

Returns hardcoded realistic responses so AutoGen agents execute fully
(AssistantAgent.run, RoundRobinGroupChat, etc.) without any LLM credentials.

The mock detects which agent is calling by inspecting the system message,
then returns the appropriate JSON response.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Mapping, Optional, Sequence

from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    RequestUsage,
    SystemMessage,
)
from autogen_core import CancellationToken
from autogen_core.tools import Tool, ToolSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded responses per agent
# ---------------------------------------------------------------------------

_PLANNER_INITIAL = json.dumps({
    "units": [
        {
            "id": "app_rg",
            "type": "resource_group",
            "depends_on": [],
            "constraints": {
                "required_rg": None,
                "forbidden_rg": None,
                "location": "eastus2"
            }
        },
        {
            "id": "postgres_flex",
            "type": "postgres_flex",
            "depends_on": ["app_rg"],
            "constraints": {
                "required_rg": "rg-payments-api-prod",
                "forbidden_rg": "app_rg",
                "location": "eastus2"
            }
        },
        {
            "id": "storage_account",
            "type": "storage_account",
            "depends_on": ["app_rg"],
            "constraints": {
                "required_rg": "rg-payments-api-prod",
                "forbidden_rg": None,
                "location": "eastus2"
            }
        }
    ],
    "questions": [
        "Resource group **rg-payments-api-prod** already exists in the subscription. "
        "Should the Terraform use this existing resource group, or create a new one?"
    ]
})

_PLANNER_FINAL = json.dumps({
    "units": [
        {
            "id": "app_rg",
            "type": "resource_group",
            "depends_on": [],
            "constraints": {
                "required_rg": None,
                "forbidden_rg": None,
                "location": "eastus2"
            }
        },
        {
            "id": "postgres_flex",
            "type": "postgres_flex",
            "depends_on": ["app_rg"],
            "constraints": {
                "required_rg": "rg-payments-api-prod",
                "forbidden_rg": "app_rg",
                "location": "eastus2"
            }
        },
        {
            "id": "storage_account",
            "type": "storage_account",
            "depends_on": ["app_rg"],
            "constraints": {
                "required_rg": "rg-payments-api-prod",
                "forbidden_rg": None,
                "location": "eastus2"
            }
        }
    ],
    "questions": []
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
    "main_tf": '''module "{unit_id}" {{
  source = "git::https://github.com/natesanshreyas/terraform-azure-modules.git//modules/{unit_type}?ref=abc1234"

  resource_group_name = var.resource_group_name
  location            = var.location
  name                = "{unit_id}"

  tags = {{
    environment = var.environment
    ticket_id   = var.ticket_id
    cost_center = var.cost_center
  }}
}}''',
    "variables_tf": '''variable "resource_group_name" {{ type = string }}
variable "location"            {{ type = string  default = "eastus2" }}
variable "environment"         {{ type = string }}
variable "ticket_id"           {{ type = string }}
variable "cost_center"         {{ type = string  default = "fin-ops" }}
'''
})


def _detect_agent(messages: Sequence[LLMMessage]) -> str:
    for m in messages:
        if isinstance(m, SystemMessage):
            content = m.content.lower()
            if "infrastructure planner" in content:
                # HITL resume path has human answers in user message
                for msg in messages:
                    if hasattr(msg, 'content') and "incorporate these" in str(msg.content).lower():
                        return "planner_final"
                return "planner_initial"
            if "terraform code generator" in content:
                return "tf_generator"
            if "repository resolver" in content:
                return "gh_search"
    return "unknown"


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------

class MockModelClient(ChatCompletionClient):
    """Returns hardcoded responses — lets AutoGen agents run without credentials."""

    @property
    def capabilities(self):
        from autogen_core.models import ModelCapabilities
        return ModelCapabilities(vision=False, function_calling=False, json_output=True)

    @property
    def model_info(self):
        from autogen_core.models import ModelInfo
        return ModelInfo(vision=False, function_calling=False, json_output=True, family="mock")

    async def create(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Any = "auto",
        json_output: Any = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Optional[CancellationToken] = None,
    ) -> CreateResult:
        agent = _detect_agent(messages)
        logger.info("MockModelClient: responding as agent=%s", agent)

        if agent == "planner_initial":
            content = _PLANNER_INITIAL
        elif agent == "planner_final":
            content = _PLANNER_FINAL
        elif agent == "gh_search":
            content = _GH_SEARCH
        elif agent == "tf_generator":
            content = _TF_OUTPUT
        else:
            content = "{}"

        return CreateResult(
            finish_reason="stop",
            content=content,
            usage=RequestUsage(prompt_tokens=100, completion_tokens=200),
            cached=False,
        )

    async def create_stream(self, messages, **kwargs) -> AsyncGenerator:
        result = await self.create(messages, **kwargs)
        yield result

    def actual_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=0, completion_tokens=0)

    def total_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=0, completion_tokens=0)

    def count_tokens(self, messages, **kwargs) -> int:
        return 100

    def remaining_tokens(self, messages, **kwargs) -> int:
        return 100000

    async def close(self) -> None:
        pass


def get_model_client():
    """Return a real LLM client, or MockModelClient if MOCK_LLM=true.

    Priority:
      1. Azure OpenAI  — set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT_NAME
      2. OpenAI        — set OPENAI_API_KEY (uses gpt-4o by default)
      3. Mock (dev)    — set MOCK_LLM=true  (returns hardcoded responses, no LLM calls)

    Raises RuntimeError if no credentials and MOCK_LLM is not set.
    """
    import os

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    endpoint   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "")
    if endpoint and deployment:
        from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
        logger.info("Using Azure OpenAI: endpoint=%s deployment=%s", endpoint, deployment)
        return AzureOpenAIChatCompletionClient(
            azure_deployment=deployment,
            azure_endpoint=endpoint,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
        )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        from autogen_ext.models.openai import OpenAIChatCompletionClient
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        logger.info("Using OpenAI: model=%s", model)
        return OpenAIChatCompletionClient(model=model, api_key=openai_key)

    # ── Mock (explicit dev opt-in only) ───────────────────────────────────────
    if os.environ.get("MOCK_LLM", "false").lower() == "true":
        logger.info("MOCK_LLM=true — using MockModelClient (no real LLM calls)")
        return MockModelClient()

    raise RuntimeError(
        "No LLM credentials configured.\n"
        "  Azure OpenAI: set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT_NAME\n"
        "  OpenAI:       set OPENAI_API_KEY\n"
        "  Local dev:    set MOCK_LLM=true  (returns hardcoded responses)"
    )
