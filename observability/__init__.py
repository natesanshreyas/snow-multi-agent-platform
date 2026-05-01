"""
observability package — wires Microsoft Agent Framework telemetry + safety
middleware into the platform.

Modules
-------
- telemetry.py : OpenTelemetry / Azure Monitor setup via
                 agent_framework.observability.configure_otel_providers
- middleware.py: ContentSafetyMiddleware, AuditFunctionMiddleware, and
                 default_middleware() for one-stop wiring on every agent.

Usage in an agent factory:

    from observability import default_middleware, setup_telemetry
    setup_telemetry()
    agent = client.as_agent(
        name="my_agent",
        instructions="...",
        tools=[...],
        middleware=default_middleware(),
    )
"""

from .middleware import (
    AuditFunctionMiddleware,
    ContentSafetyMiddleware,
    default_middleware,
)
from .telemetry import setup_telemetry

__all__ = [
    "AuditFunctionMiddleware",
    "ContentSafetyMiddleware",
    "default_middleware",
    "setup_telemetry",
]
