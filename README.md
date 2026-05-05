# snow-multi-agent-platform

ServiceNow → Terraform provisioning platform powered by four AI agents. A ticket comes in, gets routed to the right cloud, planned into infrastructure units, searched for Terraform modules, generated, evaluated, and a PR is opened — all automatically, with two human checkpoints along the way.

---

## What it does

```
ServiceNow ticket
        │
        ▼
  Router Agent         classifies ticket → azure / aws / snowflake
        │
        ▼
  Planner Agent        decomposes ticket into infra units (e.g. VPC, RDS, S3)
        │              raises questions if anything is ambiguous
        │
        ◆ HITL 1       engineer answers planner questions in ServiceNow
        │
        ▼
  Environment scan     reads existing Azure/AWS/Snowflake state
        │
        ▼
  Planner Agent        finalizes plan with human answers + scan results
        │
        ◆ HITL 2       engineer approves estimated monthly cost + quota check
        │
        ▼
  GitHub Search Agent  finds the right Terraform module repo for each unit type
        │
        ▼
  TF Generator Agent   generates main.tf + variables.tf per unit
        │              evaluators score each file (correctness / security / compliance)
        │              retries with evaluator feedback until all pass
        │
        ▼
  PR opened            HCL pushed to feature branch, PR opened, SNOW ticket updated
```

---

## Agents

| Agent | File | How it calls the LLM |
|---|---|---|
| Router | `orchestrator/router_agent.py` | Direct Azure OpenAI call + keyword heuristic fallback |
| Planner | `agents/azure/planner_agent.py` | MS Agent Framework SDK — `client.as_agent().run()` |
| GitHub Search | `agents/github_search_agent.py` | MS Agent Framework SDK — `client.as_agent().run()` |
| TF Generator | `agents/azure/terraform_agent.py` | MS Agent Framework SDK — `client.as_agent().run()` |

All orchestration runs **in-process**. The MS Agent Framework SDK manages the agent loop; your FastAPI pod is the only compute. Azure AI Foundry is used for OpenTelemetry tracing only (optional — set `AZURE_AI_PROJECT_ENDPOINT` to enable).

---

## How the LLM is called (`agents/client.py`)

```python
from agent_framework.openai import OpenAIChatClient
from azure.identity import DefaultAzureCredential

client = OpenAIChatClient(
    model=os.environ["AZURE_AI_MODEL_DEPLOYMENT"],
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    credential=DefaultAzureCredential(),   # workload identity on AKS, az login locally
)

agent = client.as_agent(name="azure_planner", instructions="...")
result = await agent.run(message)
raw = result.text
```

No API keys in config. `DefaultAzureCredential` picks up workload identity on AKS and `az login` locally.

---

## Running locally

**No Azure credentials? Use mock mode:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
MOCK_LLM=true DEMO_MODE=true python -m uvicorn demo_server:app --port 8001 --reload
```

**With real Azure OpenAI:**
```bash
cp .env.example .env   # fill in AZURE_OPENAI_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT
az login               # DefaultAzureCredential picks this up automatically
python -m uvicorn demo_server:app --port 8001 --reload
```

**Submit a ticket:**
```bash
curl -X POST http://localhost:8001/demo/submit \
  -H "Content-Type: application/json" \
  -d '{
    "short_description": "Provision AWS infrastructure for analytics service",
    "description": "VPC in us-east-1, RDS PostgreSQL (db.t3.medium, Multi-AZ), S3 bucket with versioning.",
    "requested_by": "your-name"
  }'
# → {"run_id": "...", "cloud": "aws", "routing_reasoning": "..."}
```

**Answer the HITL question:**
```bash
curl -X POST http://localhost:8001/demo/resume/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"answers": {"vpc": "use existing"}}'
```

**Approve cost:**
```bash
curl -X POST "http://localhost:8001/demo/resume/<run_id>?approve_cost=true" \
  -H "Content-Type: application/json" -d '{}'
```

**Poll status:**
```bash
curl http://localhost:8001/runs/<run_id>
```

**Interactive API docs:** http://localhost:8001/docs

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Yes (or `MOCK_LLM=true`) | Azure OpenAI endpoint URL |
| `AZURE_AI_MODEL_DEPLOYMENT` | Yes (or `MOCK_LLM=true`) | Deployment name, e.g. `gpt-4o` |
| `AZURE_OPENAI_API_KEY` | No | Omit to use managed identity / `az login` |
| `GITHUB_ORG` | Yes for real PRs | GitHub org containing Terraform module repos |
| `GITHUB_MODULES_REPO` | Yes for real PRs | Repo name containing the modules |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Yes for real PRs | PAT with `repo` scope |
| `MOCK_LLM` | No | Set `true` to run with no credentials — simulates all LLM responses |
| `DEMO_MODE` | No | Set `true` to stub GitHub/ServiceNow API calls |
| `AZURE_AI_PROJECT_ENDPOINT` | No | Enables OpenTelemetry traces in Azure AI Foundry portal |

---

## Deploying to AKS

### One-time cluster setup

Your AKS cluster needs two addons enabled (if not already):

```bash
az aks update \
  --name <your-cluster> --resource-group <your-rg> \
  --enable-oidc-issuer --enable-workload-identity

az aks update \
  --name <your-cluster> --resource-group <your-rg> \
  --attach-acr <your-acr>
```

### Deploy in 2 steps

**Step 1 — fill in your values:**
```bash
cp .deploy.env.example .deploy.env
# Open .deploy.env and set your 6 values (cluster, rg, acr, openai endpoint, model, github org)
```

**Step 2 — run the script:**
```bash
./deploy.sh
```

Done. The script takes ~2 minutes and handles everything:

1. Creates a user-assigned managed identity
2. Reads the OIDC issuer from your AKS cluster and creates a federated credential — this links the Kubernetes `ServiceAccount` to the managed identity, so the pod can call Azure OpenAI with no API key
3. Assigns `Cognitive Services OpenAI User` role to the identity on your OpenAI resource
4. Builds and pushes the image using `az acr build` (no local Docker needed)
5. Injects all your values into the k8s manifests and applies them in order
6. Waits for the rollout and prints the ingress IP

**Verify:**
```bash
curl http://<ingress-ip>/health
# → {"status":"ok"}
```

### Helm (alternative to deploy.sh)

If your org uses Helm:

```bash
helm upgrade --install snow-multi-agent helm/snow-multi-agent \
  --set image.repository=<ACR>.azurecr.io/snow-multi-agent \
  --set workloadIdentity.clientId=<managed-identity-client-id> \
  --set config.AZURE_OPENAI_ENDPOINT=https://<hub>.openai.azure.com/ \
  --set config.AZURE_CLIENT_ID=<client-id> \
  --set config.AZURE_TENANT_ID=<tenant-id> \
  --set secrets.GITHUB_PERSONAL_ACCESS_TOKEN=<token>
```
