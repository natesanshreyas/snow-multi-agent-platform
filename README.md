# snow-multi-agent-platform

ServiceNow → Terraform multi-agent provisioning platform using **Microsoft Agent Framework SDK + Azure OpenAI**.

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

All agent orchestration runs **in-process** via the MS Agent Framework SDK. Azure AI Foundry is used only for OpenTelemetry tracing (optional).

---

## Agent pattern (`agents/client.py`)

All agents use Microsoft Agent Framework SDK with Azure OpenAI backed by managed identity:

```python
from agent_framework.openai import OpenAIChatClient
from azure.identity import DefaultAzureCredential

client = OpenAIChatClient(
    model=os.environ["AZURE_AI_MODEL_DEPLOYMENT"],
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

agent = client.as_agent(name="azure_planner", instructions="...")
result = await agent.run(ticket_message)
raw = result.text
```

`DefaultAzureCredential` picks up workload identity on AKS and falls back to `az login` locally — no API keys in config.

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

### Mock mode (no credentials)

```bash
MOCK_LLM=true DEMO_MODE=true python -m uvicorn demo_server:app --port 8001 --reload
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

---

## Deploying to AKS

### Prerequisites

- AKS cluster with **OIDC issuer** and **workload identity** addon enabled:
  ```bash
  az aks update \
    --name <your-cluster> \
    --resource-group <your-rg> \
    --enable-oidc-issuer \
    --enable-workload-identity
  ```
- ACR attached to the cluster:
  ```bash
  az aks update \
    --name <your-cluster> \
    --resource-group <your-rg> \
    --attach-acr <your-acr>
  ```

### Deploy (3 steps)

**1. Fill in your config:**
```bash
cp .deploy.env.example .deploy.env
# Edit .deploy.env — 6 values to set
```

**2. Run the deploy script:**
```bash
./deploy.sh
```

That's it. The script handles everything: creates the managed identity, wires workload identity, assigns the OpenAI role, builds and pushes the image via `az acr build` (no local Docker needed), and applies all k8s manifests.

**3. Verify:**
```bash
curl http://<ingress-ip>/health
# → {"status":"ok"}
```

### What deploy.sh does

1. Creates a user-assigned managed identity
2. Reads the AKS OIDC issuer URL and creates a federated credential (links the K8s `ServiceAccount` to the managed identity — no secrets involved)
3. Assigns `Cognitive Services OpenAI User` role to the identity on your OpenAI resource
4. Builds and pushes the image using `az acr build`
5. Substitutes all placeholder values into the k8s manifests and applies them in order
6. Waits for the rollout to complete

### Helm (alternative to deploy.sh)

If you prefer Helm over the script:

```bash
helm upgrade --install snow-multi-agent helm/snow-multi-agent \
  --set image.repository=<ACR>.azurecr.io/snow-multi-agent \
  --set workloadIdentity.clientId=<managed-identity-client-id> \
  --set config.AZURE_OPENAI_ENDPOINT=https://<hub>.openai.azure.com/ \
  --set config.AZURE_CLIENT_ID=<client-id> \
  --set config.AZURE_TENANT_ID=<tenant-id> \
  --set secrets.GITHUB_PERSONAL_ACCESS_TOKEN=<token>
```
