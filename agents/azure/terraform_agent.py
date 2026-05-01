"""
terraform_agent.py — Generates Terraform HCL using Microsoft Agent Framework
with real `agent_framework.tool` wrappers around the GitHub MCP fetchers.

The agent is wired with two tools it can call autonomously:
  - read_module_readme(module_type, org, modules_repo) -> str
  - get_latest_module_version(module_type, org, modules_repo) -> str

It receives the unit spec + ticket context in the user message, fetches the
README and pinned commit SHA on its own, and returns
{"main_tf": "...", "variables_tf": "..."} as JSON.

Eval-driven retries (correctness / security / compliance) are managed in the
workflow loop here; on failure the loop calls agent.run() again with feedback
appended to the user message.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from agent_framework import tool

from agents.client import get_model_client
from mcp.github import (
    get_latest_module_version as _get_latest_module_version_raw,
    read_module_readme as _read_module_readme_raw,
)
from orchestrator.models import EvaluatorResult, PlanUnit, WorkflowRun

logger = logging.getLogger(__name__)

MAX_EVAL_RETRIES = 2


@dataclass
class TerraformOutput:
    unit_id: str
    main_tf: str
    variables_tf: str
    eval_results: List[EvaluatorResult]
    passed: bool
    module_version: str = "main"


EvaluatorFn = Callable[[str, str, str], EvaluatorResult]

_SYSTEM_PROMPT = """\
You are a Terraform code generator for Azure infrastructure.

You have two tools:
  - read_module_readme(module_type, org, modules_repo) -> str
        Returns the modules/{module_type}/README.md so you can identify the
        required vs optional variables.
  - get_latest_module_version(module_type, org, modules_repo) -> str
        Returns the commit SHA on main that you must pin in the module
        source URL.

For the unit you are given, call BOTH tools first, then generate
syntactically correct main.tf and variables.tf.

=== RULES ===

1. Use module blocks exclusively — never raw resource blocks. (ENFORCED by evaluator)
2. Pin module source to the commit SHA returned by get_latest_module_version:
     source = "git::https://github.com/{org}/{modules_repo}.git//modules/{type}?ref={sha}"
3. Consult the README to identify ALL required variables and their types.
   Do not omit required variables. Do not invent variable names.
4. Apply all constraints from the unit spec exactly.
5. Tag every resource with: cost_center (from ticket), ticket_id, environment.
6. Storage account names: ≤24 chars, lowercase, alphanumeric only.
7. Default location: eastus2 unless overridden by constraints.location.
8. If evaluation feedback is provided, fix ONLY the reported issues — do not
   regenerate the entire file from scratch.

=== OUTPUT FORMAT ===
Output ONLY this JSON — no prose, no markdown fences:
{
  "main_tf": "<full HCL content as a single JSON string>",
  "variables_tf": "<full HCL content as a single JSON string>"
}
"""


# Agent Framework tool wrappers around the raw MCP fetchers.
read_module_readme_tool = tool(
    _read_module_readme_raw,
    name="read_module_readme",
    description=(
        "Fetch modules/{module_type}/README.md from a GitHub repo. "
        "Returns the README content as plain text. "
        "Falls back to a stub if the file is missing."
    ),
)

get_latest_module_version_tool = tool(
    _get_latest_module_version_raw,
    name="get_latest_module_version",
    description=(
        "Return the latest commit SHA on main that touched modules/{module_type}/ "
        "in the given repo. Use this SHA as the ?ref= value of the module source URL."
    ),
)


def _build_user_message(
    unit: PlanUnit,
    ticket_id: str,
    environment: str,
    org: str,
    modules_repo: str,
    feedback: Optional[str],
) -> str:
    unit_spec = {
        "id": unit.id,
        "type": unit.type,
        "constraints": {
            "required_rg": unit.constraints.required_rg,
            "forbidden_rg": unit.constraints.forbidden_rg,
            "location": unit.constraints.location,
            **unit.constraints.extra,
        },
    }

    parts = [
        "Generate Terraform for the following infrastructure unit. "
        "Call read_module_readme and get_latest_module_version FIRST, "
        "then produce main.tf + variables.tf.",
        f"\n=== Unit spec ===\n{json.dumps(unit_spec, indent=2)}",
        "\n=== Context ===",
        f"ticket_id: {ticket_id}",
        f"environment: {environment}",
        f"module source org: {org}",
        f"modules repo: {modules_repo}",
    ]

    if feedback:
        parts.append(
            f"\n=== Evaluator feedback from previous attempt — fix these issues ===\n{feedback}"
        )

    return "\n".join(parts)


def _parse_terraform_output(raw: str) -> tuple[str, str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"TF agent returned non-JSON: {raw[:300]}")
        data = json.loads(match.group(0))

    main_tf = data.get("main_tf", "")
    variables_tf = data.get("variables_tf", "")

    if not main_tf:
        raise ValueError("TF agent returned empty main_tf")

    return main_tf, variables_tf


async def run_terraform_agent(
    unit: PlanUnit,
    run: WorkflowRun,
    evaluators: List[EvaluatorFn],
    org: str,
    modules_repo: str,
) -> TerraformOutput:
    """Run the TF Generator agent with read-readme + get-sha tools, eval, retry."""
    ticket_id = run.request.ticket_id if run.request else "UNKNOWN"
    environment = run.request.environment if run.request else "dev"

    repo = unit.resolved_repo or modules_repo

    # Pin module SHA for the output record (informational).
    # The agent will also fetch this independently via its tool.
    module_sha = await _get_latest_module_version_raw(unit.type, org, repo)

    client = get_model_client()
    agent = client.as_agent(
        name="azure_tf_generator",
        instructions=_SYSTEM_PROMPT,
        tools=[read_module_readme_tool, get_latest_module_version_tool],
    )

    feedback: Optional[str] = None
    last_output: Optional[TerraformOutput] = None

    for attempt in range(1, MAX_EVAL_RETRIES + 2):
        user_message = _build_user_message(
            unit=unit,
            ticket_id=ticket_id,
            environment=environment,
            org=org,
            modules_repo=repo,
            feedback=feedback,
        )

        result = await agent.run(user_message)
        raw = result.text
        main_tf, variables_tf = _parse_terraform_output(raw)

        eval_results = [ev(main_tf, variables_tf, ticket_id) for ev in evaluators]
        passed = all(r.passed for r in eval_results)

        last_output = TerraformOutput(
            unit_id=unit.id,
            main_tf=main_tf,
            variables_tf=variables_tf,
            eval_results=eval_results,
            passed=passed,
            module_version=module_sha,
        )

        if passed:
            logger.info(
                "unit=%s terraform passed all evaluators on attempt %d (sha=%s)",
                unit.id, attempt,
                module_sha[:7] if module_sha != "main" else "main",
            )
            break

        failed = [r for r in eval_results if not r.passed]
        feedback = "\n".join(f"{r.evaluator} ({r.score}/5): {r.reason}" for r in failed)
        logger.warning(
            "unit=%s eval failed attempt %d/%d — feedback: %s",
            unit.id, attempt, MAX_EVAL_RETRIES + 1, feedback,
        )

        if attempt == MAX_EVAL_RETRIES + 1:
            logger.error("unit=%s exhausted all eval retries", unit.id)
            break

    if not last_output:
        raise RuntimeError(f"unit={unit.id}: no output generated")

    unit.terraform_output = last_output.main_tf
    return last_output
