from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SIGNALS: dict[str, list[str]] = {
    "azure": [
        "azure kubernetes service", "azure container instances", "azure data factory",
        "azure devops", "azure sql", "azure storage", "azure function",
        "cosmos db", "blob storage", "service bus", "key vault", "app service",
        "resource group", "azure postgres", "postgres flexible server",
        "azure", "aks", "aci", "vnet", "arm template", "bicep",
    ],
    "aws": [
        "amazon web services", "elastic kubernetes service", "elastic container service",
        "cloudformation", "cloudwatch", "elasticache", "dynamodb",
        "ec2", "rds", "lambda", "s3", "vpc", "ecs", "eks",
        "iam", "route53", "aurora", "kinesis", "glue", "redshift",
        "fargate", "beanstalk", "lightsail", "alb", "elb", "sqs", "sns",
        "aws",
    ],
    "snowflake": [
        "snowflake data cloud", "snowflake warehouse", "snowflake database",
        "snowflake schema", "snowflake stage", "snowflake pipe",
        "snowpark", "snowsight", "snowflake role", "snowflake account",
        "data cloud", "snowflake", "data warehouse",
    ],
}


def _heuristic_route(text: str) -> tuple[str, str]:
    text_lower = text.lower()
    scores: dict[str, int] = {c: 0 for c in _SIGNALS}

    matched: dict[str, list[str]] = {c: [] for c in _SIGNALS}
    for cloud, signals in _SIGNALS.items():
        for signal in signals:
            if signal in text_lower:
                scores[cloud] += len(signal.split())
                matched[cloud].append(signal)

    best = max(scores, key=lambda c: scores[c])
    if scores[best] == 0:
        return "azure", "No cloud-specific terms detected; defaulting to Azure IaC workflow."

    hits = matched[best][:3]
    return best, (
        f"Detected {best.upper()} signals in ticket: {', '.join(repr(h) for h in hits)}. "
        f"Routing to {best.upper()} IaC workflow."
    )


_SYSTEM = (
    "You are a cloud infrastructure routing agent inside a ServiceNow automation platform. "
    "Your only job is to read a ticket and decide which cloud platform it targets."
)

_USER_TMPL = """\
Analyze the ServiceNow ticket below and determine the target cloud platform.

Output format (two lines, nothing else):
Line 1: exactly one of:  azure  |  aws  |  snowflake
Line 2: one sentence explaining the key signals that led to this decision.

Ticket:
{ticket_text}
"""


async def _llm_route(ticket_text: str) -> Optional[tuple[str, str]]:
    from agents.client import get_model_client

    try:
        client = get_model_client()
    except RuntimeError:
        logger.debug("LLM not configured — skipping LLM routing")
        return None

    agent = client.as_agent(
        name="cloud_router",
        instructions=_SYSTEM,
    )

    try:
        result = await agent.run(_USER_TMPL.format(ticket_text=ticket_text))
        raw = result.text.strip()
        lines = raw.split("\n", 1)
        cloud_raw = lines[0].strip().lower()
        reason = lines[1].strip() if len(lines) > 1 else "LLM decision."

        for c in ("azure", "aws", "snowflake"):
            if c in cloud_raw:
                logger.info("LLM router → %s: %s", c, reason)
                return c, reason

        logger.warning("LLM router returned unrecognised cloud: %r", cloud_raw)
        return None

    except Exception as exc:
        logger.warning("LLM router error: %s", exc)
        return None


async def route_ticket(short_description: str, description: str) -> tuple[str, str]:
    """Determine cloud target. Returns (cloud, reasoning).

    cloud     — "azure" | "aws" | "snowflake"
    reasoning — human-readable sentence shown in the demo UI
    """
    ticket_text = f"Short description: {short_description}\n\nDescription:\n{description}"

    result = await _llm_route(ticket_text)
    if result:
        cloud, reason = result
        return cloud, f"AI Router: {reason}"

    cloud, reason = _heuristic_route(ticket_text)
    return cloud, f"Router Agent: {reason}"
