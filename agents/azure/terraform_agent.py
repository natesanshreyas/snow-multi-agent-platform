from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from agents.client import get_model_client
from mcp.github import get_latest_module_version, read_module_readme
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

Given an infrastructure unit spec, a module README, and the module's latest
commit SHA, generate syntactically correct main.tf and variables.tf.

=== RULES ===

1. Use module blocks exclusively — never raw resource blocks. (ENFORCED by evaluator)
2. Pin module source to the provided commit SHA:
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


async def _fetch_module_context(
    unit_type: str,
    org: str,
    modules_repo: str,
) -> tuple[str, str]:
    import asyncio
    readme, sha = await asyncio.gather(
        read_module_readme(unit_type, org, modules_repo),
        get_latest_module_version(unit_type, org, modules_repo),
    )
    return readme, sha


def _build_user_message(
    unit: PlanUnit,
    ticket_id: str,
    environment: str,
    org: str,
    modules_repo: str,
    module_readme: str,
    module_sha: str,
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
        f"Generate Terraform for the following infrastructure unit.",
        f"\n=== Unit spec ===\n{json.dumps(unit_spec, indent=2)}",
        f"\n=== Context ===",
        f"ticket_id: {ticket_id}",
        f"environment: {environment}",
        f"module source org: {org}",
        f"modules repo: {modules_repo}",
        f"module commit SHA (use as ?ref= value): {module_sha}",
        f"\n=== Module README (defines required and optional variables) ===\n{module_readme}",
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
    ticket_id = run.request.ticket_id if run.request else "UNKNOWN"
    environment = run.request.environment if run.request else "dev"

    repo = unit.resolved_repo or modules_repo

    module_readme, module_sha = await _fetch_module_context(unit.type, org, repo)
    logger.info(
        "unit=%s module=%s sha=%s readme=%d chars",
        unit.id, unit.type, module_sha[:7] if module_sha != "main" else "main", len(module_readme),
    )

    client = get_model_client()
    agent = client.as_agent(
        name="azure_tf_generator",
        instructions=_SYSTEM_PROMPT,
    )

    feedback: Optional[str] = None
    last_output: Optional[TerraformOutput] = None

    for attempt in range(1, MAX_EVAL_RETRIES + 2):
        user_message = _build_user_message(
            unit=unit,
            ticket_id=ticket_id,
            environment=environment,
            org=org,
            modules_repo=modules_repo,
            module_readme=module_readme,
            module_sha=module_sha,
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
                unit.id, attempt, module_sha[:7] if module_sha != "main" else "main",
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
