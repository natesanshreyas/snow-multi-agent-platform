from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from agents.client import get_model_client
from orchestrator.models import Plan, PlanUnit, SnowRequest, UnitConstraints

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an Azure infrastructure planner. Given a ServiceNow provisioning request,
decompose it into discrete infrastructure units with dependency ordering, then
identify constraints and unresolved questions.

=== RULES ===

1. Output ONLY a JSON object matching the Plan schema — no prose, no Terraform.
2. Every unit must have: id (snake_case), type, depends_on (list of ids), constraints.
3. Resource groups MUST appear before any resource that depends on them.
4. Postgres units must NEVER be placed in an app resource group (forbidden_rg: "app_rg").
5. If environment scan shows a resource group already exists, raise a HITL question:
     "Resource group '{name}' already exists. Use existing (A) or create new (B)?"
6. Add to `questions` any parameter that is ambiguous or requires human confirmation.
7. If human answers are provided, incorporate them and output a finalized plan with
   an empty `questions` list. Append "PLAN_FINALIZED" on the last line.

=== OUTPUT SCHEMA ===
{
  "units": [
    {
      "id": "string",
      "type": "string",
      "depends_on": ["string"],
      "constraints": {
        "required_rg": "string or null",
        "forbidden_rg": "string or null",
        "location": "string or null"
      }
    }
  ],
  "questions": ["string"]
}
"""


def _parse_plan(raw: str) -> Plan:
    raw = raw.replace("PLAN_FINALIZED", "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
        else:
            raise ValueError(f"Planner returned non-JSON: {raw[:300]}")

    units: List[PlanUnit] = []
    for u in data.get("units", []):
        c = u.get("constraints", {})
        units.append(PlanUnit(
            id=u["id"],
            type=u["type"],
            depends_on=u.get("depends_on", []),
            constraints=UnitConstraints(
                required_rg=c.get("required_rg"),
                forbidden_rg=c.get("forbidden_rg"),
                location=c.get("location"),
                extra={k: v for k, v in c.items()
                       if k not in ("required_rg", "forbidden_rg", "location")},
            ),
        ))

    return Plan(units=units, questions=data.get("questions", []))


def _build_user_message(
    request: SnowRequest,
    scan_results: Optional[Dict[str, Any]],
    human_answers: Optional[Dict[str, str]],
) -> str:
    parts = [
        f"ServiceNow Ticket: {request.ticket_id}",
        f"Application: {request.application or '(not specified)'}",
        f"Environment: {request.environment}",
        f"Requested by: {request.requested_by}",
        f"Short description: {request.short_description}",
        f"Description:\n{request.description}",
    ]

    if scan_results:
        parts.append(
            "\n--- Environment Scan Results ---\n"
            + json.dumps(scan_results, indent=2)
        )

    if human_answers:
        parts.append(
            "\n--- Human Answers (incorporate these and finalize the plan) ---\n"
            + "\n".join(f"Q: {q}\nA: {a}" for q, a in human_answers.items())
            + "\n\nOutput the finalized plan with an empty questions list, "
              "then append PLAN_FINALIZED on a new line."
        )

    return "\n\n".join(parts)


async def run_planner_agent(
    request: SnowRequest,
    scan_results: Optional[Dict[str, Any]] = None,
    human_answers: Optional[Dict[str, str]] = None,
) -> Plan:
    """Run the Planner Agent and return a Plan.

    Args:
        request:       The approved ServiceNow request.
        scan_results:  Environment scan output (what already exists in Azure).
        human_answers: Stored answers from the ServiceNow work note.

    Returns:
        Plan with units, dependency order, and any unresolved questions.
    """
    client = get_model_client()
    agent = client.as_agent(
        name="azure_planner",
        instructions=_SYSTEM_PROMPT,
    )

    user_content = _build_user_message(request, scan_results, human_answers)
    result = await agent.run(user_content)

    raw = result.text
    logger.info("Planner output (first 300 chars): %s", raw[:300])

    plan = _parse_plan(raw)
    if human_answers:
        plan.finalized = True
    return plan
