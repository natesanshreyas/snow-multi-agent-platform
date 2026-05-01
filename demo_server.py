"""
demo_server.py — Combined Microsoft Agent Framework + Azure AI Foundry platform.

Microsoft Agent Framework (agent-framework) runs all agent orchestration:
  client.as_agent(name=..., instructions=...) -> agent.run() loops with
  evaluator-driven retries. Replaces the prior AutoGen 0.4 implementation.

Azure AI Foundry (azure-ai-projects) provides:
  - Model deployment (Azure OpenAI backed by Foundry project)
  - Managed identity credential chain (no API keys in production)
  - OpenTelemetry tracing → agent runs visible in Foundry portal

LLM credentials (one of):
  AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT              — managed identity
  AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT + API_KEY    — explicit key
  OPENAI_API_KEY                                                  — OpenAI direct
  MOCK_LLM=true                                                   — local dev

Foundry telemetry (optional — adds tracing to Foundry portal):
  AZURE_AI_PROJECT_ENDPOINT=https://your-hub.api.azureml.ms

Usage:
    export AZURE_OPENAI_ENDPOINT=https://your-hub.openai.azure.com/
    export AZURE_AI_MODEL_DEPLOYMENT=gpt-4o
    export AZURE_AI_PROJECT_ENDPOINT=https://your-hub.api.azureml.ms   # optional tracing
    python -m uvicorn demo_server:app --port 8001 --reload
"""

import logging
import os

os.environ.setdefault("DEMO_MODE", "true")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

# Enable Foundry telemetry before any agent runs (best-effort — safe to fail)
from agents.client import enable_foundry_tracing
enable_foundry_tracing()

from orchestrator.server import app  # noqa: E402

__all__ = ["app"]
