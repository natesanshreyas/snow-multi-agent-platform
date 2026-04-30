from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_model_client():
    from agent_framework.azure import AzureOpenAIChatClient
    from azure.identity import DefaultAzureCredential

    endpoint   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT",
                                os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"))
    api_key    = os.environ.get("AZURE_OPENAI_API_KEY", "")

    if not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT is required.\n"
            "  Managed identity: AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT\n"
            "  API key:          also set AZURE_OPENAI_API_KEY"
        )

    if api_key:
        logger.info("Agent Framework client: Azure OpenAI + API key  deployment=%s", deployment)
        return AzureOpenAIChatClient(
            endpoint=endpoint,
            deployment=deployment,
            api_key=api_key,
        )

    logger.info("Agent Framework client: Azure OpenAI + managed identity  deployment=%s", deployment)
    return AzureOpenAIChatClient(
        endpoint=endpoint,
        deployment=deployment,
        credential=DefaultAzureCredential(),
    )
