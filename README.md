# snow-multi-agent-platform

ServiceNow → Terraform multi-agent provisioning platform using **AutoGen 0.4 + Azure AI Foundry together**.

AutoGen handles all agent orchestration. Azure AI Foundry provides the model deployment, managed identity credential chain, and portal observability. Neither is exclusive — they solve different problems.

---

## Why both?

| | AutoGen (`autogen-agentchat`) | Foundry (`azure-ai-projects`) |
|---|---|---|
| What it does | Multi-agent orchestration patterns | Model hosting, credentials, observability |
| `RoundRobinGroupChat` | ✅ | ❌ |
| `UserProxyAgent` (HITL) | ✅ | ❌ |
| Evaluator retry loop | ✅ | ❌ |
| Managed identity (no API keys) | ❌ | ✅ |
| Foundry portal tracing | ❌ | ✅ |
| Model deployment management | ❌ | ✅ |

AutoGen is the framework. Foundry is the platform. They're designed to be used together.

---

## How they connect (`agents/client.py`)

```python
# azure-ai-projects: Foundry project connection + credential chain
project_client = AIProjectClient(
    endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),   # managed identity / az login
)

# azure-identity: token provider for Azure OpenAI (no API key needed)
token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default",
)

# autogen-ext: AutoGen-compatible client backed by Foundry-managed Azure OpenAI
model_client = AzureOpenAIChatCompletionClient(
    azure_deployment="gpt-4o",
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    azure_ad_token_provider=token_provider,   # from Foundry credential chain
)

# autogen-agentchat: agent orchestration — same patterns as always
agent = AssistantAgent(name="azure_planner", model_client=model_client, ...)
result = await agent.run(task=ticket_details)
```

Foundry telemetry (traces visible in Foundry portal):
```python
# demo_server.py calls this at startup
connection_string = project_client.telemetry.get_connection_string()
configure_azure_monitor(connection_string=connection_string)
# AutoGen runs now appear under Tracing in the Foundry portal
```

---

## The three AutoGen agents

All three use the same AutoGen patterns as the pure AutoGen version, but the model client is backed by a Foundry-managed Azure OpenAI deployment.

### Agent 1 — Planner (`agents/azure/planner_agent.py`)

```python
# Initial turn
agent = AssistantAgent(name="azure_planner", model_client=get_model_client(), ...)
result = await agent.run(task=ticket_message)

# HITL resume — RoundRobinGroupChat so the framework sees a real human turn
planner = AssistantAgent(name="azure_planner", ...)
human_proxy = UserProxyAgent(name="human_approver", input_func=_stored_answers_fn)
team = RoundRobinGroupChat([planner, human_proxy], termination_condition=MaxMessageTermination(6))
result = await team.run(task=ticket_with_answers)
```

### Agent 2 — GitHub Search (`agents/github_search_agent.py`)

```python
agent = AssistantAgent(name="github_search_agent", model_client=get_model_client(), ...)
result = await agent.run(task=search_summary)
```

### Agent 3 — Terraform Generator (`agents/azure/terraform_agent.py`)

```python
agent = AssistantAgent(name="azure_tf_generator", model_client=get_model_client(), ...)

for attempt in range(MAX_EVAL_RETRIES + 2):
    result = await agent.run(task=build_prompt(unit, feedback=feedback))
    main_tf, variables_tf = parse(result.messages[-1].content)

    eval_results = [ev(main_tf, variables_tf, ticket_id) for ev in evaluators]
    if all(r.passed for r in eval_results):
        break
    feedback = format_failures(eval_results)
```

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
python -m uvicorn demo_server:app --port 8001 --reload
```

No credentials at all? Use `MOCK_LLM=true` — AutoGen still runs end-to-end, the LLM returns hardcoded responses.

```bash
MOCK_LLM=true python -m uvicorn demo_server:app --port 8001 --reload
```

---

## Repo comparison

| Repo | SDK | Agent runtime | Model | Tracing |
|---|---|---|---|---|
| [snow-multi-agent-autogen](https://github.com/natesanshreyas/snow-multi-agent-autogen) | `autogen-agentchat` | In-process | Any OpenAI-compatible | App logs |
| [snow-multi-agent-foundry](https://github.com/natesanshreyas/snow-multi-agent-foundry) | `azure-ai-projects` | Azure (Foundry manages it) | Foundry deployment | Foundry portal |
| **snow-multi-agent-platform** (this repo) | **Both** | **In-process (AutoGen)** | **Foundry deployment** | **Foundry portal** |

This repo is the recommended production starting point — you get AutoGen's orchestration patterns and Foundry's managed infrastructure.
