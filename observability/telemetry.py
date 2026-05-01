"""
observability/telemetry.py — One-call OpenTelemetry setup for the platform.

Wraps `agent_framework.observability.configure_otel_providers` and adds
optional Azure Monitor wiring. Idempotent: safe to call from many entry
points (the orchestrator server, smoke tests, agent modules on import).

Environment variables
---------------------
ENABLE_TELEMETRY                       "true" → call setup. Default off.
ENABLE_CONSOLE_EXPORTERS               "true" → also print spans to stdout (dev).
APPLICATIONINSIGHTS_CONNECTION_STRING  if set → use Azure Monitor exporter.
OTEL_EXPORTER_OTLP_ENDPOINT            standard OTLP endpoint (Jaeger / Tempo).
ENABLE_SENSITIVE_DATA                  "true" → include prompt/response text in
                                       spans (dev only — never enable in prod
                                       with PII).

The Microsoft Agent Framework instruments every `Agent.run()`, every
`FunctionTool` invocation, and every chat-client call automatically once
`enable_instrumentation()` has been called — so wiring this once gives us
end-to-end traces (planner → github_search → terraform_agent → tool calls)
out of the box.
"""

from __future__ import annotations

import logging
import os
from threading import Lock

logger = logging.getLogger(__name__)

_LOCK = Lock()
_INITIALIZED = False


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def setup_telemetry(*, force: bool = False) -> bool:
    """Configure OpenTelemetry for the Agent Framework.

    Returns True if telemetry was set up (or already running), False if
    skipped because ``ENABLE_TELEMETRY`` is not set.

    The function is idempotent — calling it multiple times is a no-op
    after the first successful run.
    """
    global _INITIALIZED

    if not force and not _truthy("ENABLE_TELEMETRY"):
        return False

    with _LOCK:
        if _INITIALIZED:
            return True

        try:
            from agent_framework.observability import (
                configure_otel_providers,
                enable_instrumentation,
            )
        except ImportError:  # pragma: no cover
            logger.warning("agent_framework.observability not available")
            return False

        sensitive = _truthy("ENABLE_SENSITIVE_DATA") or None
        console = _truthy("ENABLE_CONSOLE_EXPORTERS") or None
        appinsights_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

        if appinsights_conn:
            # Azure Monitor path: configure provider externally, then turn
            # on Agent Framework instrumentation only.
            try:
                from azure.monitor.opentelemetry import configure_azure_monitor
                configure_azure_monitor(connection_string=appinsights_conn)
                enable_instrumentation(enable_sensitive_data=sensitive)
                logger.info("telemetry: Azure Monitor + Agent Framework instrumentation enabled")
            except ImportError:
                logger.warning(
                    "APPLICATIONINSIGHTS_CONNECTION_STRING set but "
                    "azure-monitor-opentelemetry is not installed; "
                    "falling back to OTLP / console."
                )
                configure_otel_providers(
                    enable_sensitive_data=sensitive,
                    enable_console_exporters=console,
                )
        else:
            # Generic OTLP / console path. Agent Framework reads
            # OTEL_EXPORTER_OTLP_ENDPOINT etc. itself.
            configure_otel_providers(
                enable_sensitive_data=sensitive,
                enable_console_exporters=console,
            )
            logger.info("telemetry: OTLP / console exporters configured")

        _INITIALIZED = True
        return True
