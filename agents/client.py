"""
client.py — Microsoft Agent Framework chat-client factory.

Exposes:
    get_model_client()        -> AzureOpenAIChatClient | OpenAIChatClient | MockModelClient
    enable_foundry_tracing()  -> wires Azure AI Foundry OpenTelemetry exporter (best effort)

The agents (`agents/azure/planner_agent.py`, `agents/azure/terraform_agent.py`,
`agents/github_search_agent.py`) consume the returned client uniformly:

    client = get_model_client()
    agent  = client.as_agent(name=..., instructions=...)
    result = await agent.run(user_message)
    text   = result.text

For local dev without credentials set MOCK_LLM=true and the mock client is
returned (see agents/mock_client.py).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_model_client():
    """Return a Microsoft Agent Framework chat client.

    Resolution order:
      1. Azure OpenAI       - AZURE_OPENAI_ENDPOINT + (AZURE_AI_MODEL_DEPLOYMENT
                              or AZURE_OPENAI_DEPLOYMENT_NAME)
                              * API key path:  also AZURE_OPENAI_API_KEY
                              * MI path:       DefaultAzureCredential
      2. OpenAI direct      - OPENAI_API_KEY  (model = $OPENAI_MODEL or gpt-4o)
      3. Mock (dev opt-in)  - MOCK_LLM=true   -> agents.mock_client.MockModelClient
    """
    endpoint   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = (os.environ.get("AZURE_AI_MODEL_DEPLOYMENT")
                  or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", ""))

    if endpoint and deployment:
        from agent_framework.azure import AzureOpenAIChatClient
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        if api_key:
            logger.info("Agent Framework: AzureOpenAIChatClient + API key  deployment=%s", deployment)
            return AzureOpenAIChatClient(
                endpoint=endpoint,
                deployment=deployment,
                api_key=api_key,
            )
        from azure.identity import DefaultAzureCredential
        logger.info("Agent Framework: AzureOpenAIChatClient + managed identity  deployment=%s", deployment)
        return AzureOpenAIChatClient(
            endpoint=endpoint,
            deployment=deployment,
            credential=DefaultAzureCredential(),
        )

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        from agent_framework.openai import OpenAIChatClient
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        logger.info("Agent Framework: OpenAIChatClient  model=%s", model)
        return OpenAIChatClient(model=model, api_key=openai_key)

    if os.environ.get("MOCK_LLM", "false").lower() == "true":
        from agents.mock_client import MockModelClient
        logger.info("MOCK_LLM=true - returning MockModelClient")
        return MockModelClient()

    raise RuntimeError(
        "No LLM credentials configured.\n"
        "  Azure OpenAI: AZURE_OPENAI_ENDPOINT + (AZURE_AI_MODEL_DEPLOYMENT | AZURE_OPENAI_DEPLOYMENT_NAME)\n"
        "  OpenAI:       OPENAI_API_KEY\n"
        "  Local dev:    MOCK_LLM=true"
    )


def enable_foundry_tracing() -> None:
    """Wire OpenTelemetry -> Azure AI Foundry App Insights, if available.

    Safe to call unconditionally - silently no-ops when:
      * AZURE_AI_PROJECT_ENDPOINT is not set, or
      * azure-ai-projects / azure-monitor-opentelemetry are not installed, or
      * no credentials are available for the project.
    """
    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        logger.info("Foundry tracing: AZURE_AI_PROJECT_ENDPOINT not set - skipping")
        return

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        logger.info("Foundry tracing: azure-ai-projects not installed (%s)", exc)
        return

    try:
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=DefaultAzureCredential(),
        )
        conn_str = client.telemetry.get_application_insights_connection_string()  # type: ignore[attr-defined]
        if not conn_str:
            logger.info("Foundry tracing: no App Insights connected to project")
            return

        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=conn_str)
        logger.info("Foundry tracing: enabled  endpoint=%s", project_endpoint)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash the app
        logger.warning("Foundry tracing: setup failed (non-fatal): %s", exc)
