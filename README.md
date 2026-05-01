# snow-multi-agent-platform

ServiceNow → Terraform multi-agent provisioning platform using **Microsoft Agent Framework SDK + Azure AI Foundry**.

---

## Architecture

A ServiceNow ticket triggers an agentic pipeline that plans, searches, generates, and evaluates Terraform — then opens a PR back to the ticket.

| Agent | File | Responsibility |
|---|---|---|
| Router | `orchestrator/router_agent.py` | Classify ticket → azure / aws / snowflake |
| Planner | `agents/azure/planner_agent.py` | Decompose ticket into infra units + HITL questions |
| GitHub Search | `agents/github_search_agent.py` | Resolve Terraform module repo per unit type |
| Terraform Generator | `agents/azure/terraform_agent.py` | Generate main.tf + variables.tf, retry on eval failure |

Evaluators (correctness, security, compliance) are plain functions — not agents.

---

## Agent pattern (`agents/client.py`)

All agents use Microsoft Agent Framework SDK with Azure OpenAI backed by managed identity:

```python
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity import DefaultAzureCredential

client = AzureOpenAIChatClient(
    endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    deployment=os.environ["AZURE_AI_MODEL_DEPLOYMENT"],
    credential=DefaultAzureCredential(),
)

agent = client.as_agent(name="azure_planner", instructions="...")
result = await agent.run(ticket_message)
raw = result.text
```

`DefaultAzureCredential` picks up managed identity on Azure Container Apps and falls back to `az login` locally — no API keys in config.

---

## Agents

### Router (`orchestrator/router_agent.py`)

Classifies the ticket to a cloud target. Falls back to a weighted keyword heuristic if the LLM is not configured.

```python
cloud, reasoning = await route_ticket(short_description, description)
# cloud → "azure" | "aws" | "snowflake"
```

### Planner (`agents/azure/planner_agent.py`)

Decomposes the ticket into typed infra units with dependency ordering. Raises HITL questions for ambiguous resource group decisions; resumes with human answers folded into the next run.

```python
plan = await run_planner_agent(request, scan_results=scan, human_answers=answers)
# plan.units → [PlanUnit], plan.questions → [str]
```

### GitHub Search (`agents/github_search_agent.py`)

Searches the GitHub org for repos containing each module type. Short-circuits the LLM call when all types resolve to exactly one repo.

```python
repo_map = await run_github_search_agent(unit_types, org)
# {"resource_group": "terraform-azure-modules", ...}
```

### Terraform Generator (`agents/azure/terraform_agent.py`)

Generates `main.tf` + `variables.tf` per infra unit using the module README and latest commit SHA fetched at runtime. Retries up to `MAX_EVAL_RETRIES` times with evaluator feedback.

```python
output = await run_terraform_agent(unit, run, evaluators, org, modules_repo)
# output.main_tf, output.variables_tf, output.passed
```

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT
python -m uvicorn demo_server:app --port 8001 --reload
```

### Required env vars

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_AI_MODEL_DEPLOYMENT` | Deployment name (e.g. `gpt-4o`) |
| `AZURE_OPENAI_API_KEY` | Optional — omit to use managed identity |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub API access for module search + PR creation |
| `GITHUB_ORG` | GitHub org owning the Terraform module repos |

### Local auth (no managed identity)

```bash
az login
# DefaultAzureCredential picks up the az login session automatically
```
