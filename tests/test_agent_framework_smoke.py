"""
test_agent_framework_smoke.py — End-to-end smoke test for the Microsoft
Agent Framework wiring.

Run:
    cd ~/projects/snow-multi-agent-platform
    . .venv/bin/activate
    MOCK_LLM=true python tests/test_agent_framework_smoke.py

Verifies:
  1. get_model_client() returns the expected client class for each env.
  2. The Microsoft Agent Framework `Agent` is constructed via
     `client.as_agent(...)` for each agent module.
  3. run_planner_agent / run_github_search_agent / run_terraform_agent each
     execute end-to-end against MockModelClient and return the right shapes.
  4. The `agent_framework.tool` wrappers (search_module_repos_tool,
     read_module_readme_tool, get_latest_module_version_tool) are real
     `FunctionTool` instances.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Force mock client BEFORE importing agents.
os.environ["MOCK_LLM"] = "true"
os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
os.environ.pop("OPENAI_API_KEY", None)

from agent_framework import FunctionTool                               # noqa: E402

from agents.client import get_model_client                              # noqa: E402
from agents.mock_client import MockModelClient                          # noqa: E402
from agents.github_search_agent import (                                # noqa: E402
    run_github_search_agent,
    search_module_repos_tool,
)
from agents.azure.planner_agent import run_planner_agent                # noqa: E402
from agents.azure.terraform_agent import (                              # noqa: E402
    get_latest_module_version_tool,
    read_module_readme_tool,
    run_terraform_agent,
)
from evaluators.terraform_compliance import evaluate_compliance         # noqa: E402
from evaluators.terraform_correctness import evaluate_correctness       # noqa: E402
from evaluators.terraform_security import evaluate_security             # noqa: E402
from orchestrator.models import (                                       # noqa: E402
    PlanUnit,
    RequestType,
    SnowRequest,
    UnitConstraints,
    WorkflowRun,
    WorkflowStatus,
)


def _make_request() -> SnowRequest:
    return SnowRequest(
        ticket_id="REQ001",
        short_description="Provision postgres + storage",
        description="Need a Postgres Flexible Server and storage account "
                    "in eastus2 for the payments-api in prod. "
                    "Cost center: fin-ops.",
        requested_by="alice",
        approval_state="approved",
        request_type=RequestType.AZURE_INFRA,
        application="payments-api",
        environment="prod",
        github_repo="payments-api-infra",
        sys_id="sys-001",
    )


GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _ok(name: str) -> None:
    print(f"{GREEN}PASS{RESET} {name}")


def _fail(name: str, msg: str) -> None:
    print(f"{RED}FAIL{RESET} {name}: {msg}")
    raise AssertionError(f"{name}: {msg}")


# --------------------------------------------------------------------------
# 1. Client + tool wiring
# --------------------------------------------------------------------------

def test_client_factory() -> None:
    client = get_model_client()
    if not isinstance(client, MockModelClient):
        _fail("client_factory", f"expected MockModelClient, got {type(client).__name__}")
    _ok("client_factory -> MockModelClient")


def test_tools_are_function_tools() -> None:
    for name, t in (
        ("search_module_repos_tool", search_module_repos_tool),
        ("read_module_readme_tool", read_module_readme_tool),
        ("get_latest_module_version_tool", get_latest_module_version_tool),
    ):
        if not isinstance(t, FunctionTool):
            _fail("tools_are_function_tools", f"{name} is {type(t).__name__}, expected FunctionTool")
        _ok(f"tool wrapper: {name} ({t.name})")


# --------------------------------------------------------------------------
# 2. Planner — JSON plan from MockModelClient
# --------------------------------------------------------------------------

async def test_planner() -> None:
    request = _make_request()
    plan = await run_planner_agent(request=request)
    if not plan.units:
        _fail("planner", "no units returned")
    if not any(u.type == "postgres_flex" for u in plan.units):
        _fail("planner", "postgres_flex unit not in plan")
    _ok(f"planner returned {len(plan.units)} units, "
        f"{len(plan.questions)} HITL question(s)")


# --------------------------------------------------------------------------
# 3. GitHub Search Agent — JSON mapping from MockModelClient
# --------------------------------------------------------------------------

async def test_github_search() -> None:
    mapping = await run_github_search_agent(
        unit_types=["postgres_flex", "storage_account", "resource_group"],
        org="natesanshreyas",
    )
    expected = {"postgres_flex", "storage_account", "resource_group"}
    if not expected.issubset(mapping.keys()):
        _fail("github_search", f"missing keys: {expected - mapping.keys()}")
    for k, v in mapping.items():
        if not v.startswith("natesanshreyas/"):
            _fail("github_search", f"bad repo for {k}: {v}")
    _ok(f"github_search resolved {len(mapping)} types -> "
        f"{sorted(set(mapping.values()))}")


# --------------------------------------------------------------------------
# 4. Terraform Agent — main_tf + variables_tf parsed; evaluators run
# --------------------------------------------------------------------------

async def test_terraform_agent() -> None:
    request = _make_request()
    run = WorkflowRun(
        run_id="run-001",
        request=request,
        status=WorkflowStatus.EXECUTING,
    )
    unit = PlanUnit(
        id="postgres_flex",
        type="postgres_flex",
        depends_on=["app_rg"],
        constraints=UnitConstraints(
            required_rg="rg-payments-api-prod",
            forbidden_rg="app_rg",
            location="eastus2",
        ),
        resolved_repo="natesanshreyas/terraform-azure-modules",
    )

    output = await run_terraform_agent(
        unit=unit,
        run=run,
        evaluators=[evaluate_correctness, evaluate_security, evaluate_compliance],
        org="natesanshreyas",
        modules_repo="terraform-azure-modules",
    )

    if not output.main_tf:
        _fail("terraform_agent", "empty main_tf")
    if "module" not in output.main_tf:
        _fail("terraform_agent", "main_tf missing 'module' block")
    if not output.variables_tf:
        _fail("terraform_agent", "empty variables_tf")
    _ok(f"terraform_agent produced main_tf={len(output.main_tf)}b, "
        f"variables_tf={len(output.variables_tf)}b, "
        f"{len(output.eval_results)} evaluator result(s)")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

async def main() -> None:
    print("=== Microsoft Agent Framework smoke test ===")
    print(f"REPO: {REPO}")
    print(f"MOCK_LLM=true (forced)\n")

    test_client_factory()
    test_tools_are_function_tools()
    await test_planner()
    await test_github_search()
    await test_terraform_agent()

    print(f"\n{GREEN}ALL TESTS PASSED{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
