"""
client.py — Unified model client: Azure AI Foundry project + AutoGen orchestration.

azure-ai-projects  manages the Foundry project connection, credential chain,
                   and telemetry (traces appear in the Foundry portal).
autogen-agentchat  runs all agent orchestration — AssistantAgent, RoundRobinGroupChat,
                   UserProxyAgent, evaluator loops — against the Foundry-backed model.

Credential priority:
  1. Managed identity / az login  (DefaultAzureCredential, no secrets in env)
     Requires: AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT
  2. API key
     Requires: AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT + AZURE_OPENAI_API_KEY
  3. OpenAI
     Requires: OPENAI_API_KEY
  4. Mock (dev only)
     Requires: MOCK_LLM=true
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# azure-ai-projects: Foundry project client (telemetry + credential chain)
# ---------------------------------------------------------------------------

_project_client = None


def get_foundry_project_client():
    """Return the azure-ai-projects AIProjectClient singleton.

    Used for:
      - Resolving DefaultAzureCredential via the Foundry project's managed identity
      - Enabling OpenTelemetry tracing to the Foundry portal
      - (Optional) accessing Foundry evaluation API, connected Azure resources, etc.

    Returns None if AZURE_AI_PROJECT_ENDPOINT is not set — agents still work,
    they just lose Foundry telemetry.
    """
    global _project_client
    if _project_client is not None:
        return _project_client

    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        return None

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        _project_client = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        logger.info("Foundry project client initialised: %s", endpoint)
    except Exception as exc:
        logger.warning("Could not initialise Foundry project client: %s", exc)
        return None

    return _project_client


def enable_foundry_tracing() -> None:
    """Enable OpenTelemetry tracing to Azure AI Foundry (best-effort).

    When enabled, every AutoGen agent run — messages, tool calls, token usage —
    appears under Tracing in the Foundry portal. Requires AZURE_AI_PROJECT_ENDPOINT
    and the azure-monitor-opentelemetry package.
    """
    project = get_foundry_project_client()
    if project is None:
        return
    try:
        # azure-ai-projects wires OpenTelemetry → Azure Monitor for the project
        connection_string = project.telemetry.get_connection_string()
        if connection_string:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(connection_string=connection_string)
            logger.info("Foundry tracing enabled — agent runs visible in Foundry portal")
    except Exception as exc:
        logger.warning("Foundry tracing not available: %s", exc)


# ---------------------------------------------------------------------------
# AutoGen model client — backed by Foundry-managed Azure OpenAI
# ---------------------------------------------------------------------------


def get_model_client():
    """Return an AutoGen ChatCompletionClient backed by Foundry-managed Azure OpenAI.

    The AutoGen agents (AssistantAgent, RoundRobinGroupChat, UserProxyAgent) call
    this and get back a standard ChatCompletionClient. They have no knowledge of
    whether it's backed by Foundry, plain Azure OpenAI, OpenAI, or a mock — the
    interface is identical.

    Managed identity path (recommended for Container Apps / production):
      Set AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT, leave API key unset.
      DefaultAzureCredential picks up the Container App's managed identity automatically.
      No secrets needed — Foundry project client validates the credential chain.
    """
    from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

    endpoint   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT",
                                os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"))
    api_key    = os.environ.get("AZURE_OPENAI_API_KEY", "")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

    # ── Foundry / Azure OpenAI with API key ──────────────────────────────────
    if endpoint and deployment and api_key:
        logger.info("AutoGen client: Azure OpenAI + API key  deployment=%s", deployment)
        return AzureOpenAIChatCompletionClient(
            azure_deployment=deployment,
            azure_endpoint=endpoint,
            api_version=api_version,
            api_key=api_key,
        )

    # ── Foundry / Azure OpenAI with managed identity (no API key) ────────────
    if endpoint and deployment:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        logger.info("AutoGen client: Azure OpenAI + managed identity  deployment=%s", deployment)
        return AzureOpenAIChatCompletionClient(
            azure_deployment=deployment,
            azure_endpoint=endpoint,
            api_version=api_version,
            azure_ad_token_provider=token_provider,
        )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        from autogen_ext.models.openai import OpenAIChatCompletionClient
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        logger.info("AutoGen client: OpenAI  model=%s", model)
        return OpenAIChatCompletionClient(model=model, api_key=openai_key)

    # ── Mock (explicit dev opt-in) ────────────────────────────────────────────
    if os.environ.get("MOCK_LLM", "false").lower() == "true":
        from agents.mock_client import MockModelClient
        logger.info("AutoGen client: MockModelClient (MOCK_LLM=true)")
        return MockModelClient()

    raise RuntimeError(
        "No LLM credentials configured.\n"
        "  Foundry + managed identity: AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT\n"
        "  Foundry + API key:          also set AZURE_OPENAI_API_KEY\n"
        "  OpenAI:                     OPENAI_API_KEY\n"
        "  Local dev:                  MOCK_LLM=true"
    )
