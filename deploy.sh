#!/usr/bin/env bash
# deploy.sh — One-shot deploy of snow-multi-agent-platform to AKS.
#
# Usage:
#   cp .deploy.env.example .deploy.env   # fill in your 6 values
#   ./deploy.sh

set -euo pipefail

# ── Load config ───────────────────────────────────────────────────────────────
if [[ ! -f .deploy.env ]]; then
  echo "ERROR: .deploy.env not found."
  echo "  cp .deploy.env.example .deploy.env  then fill in your values."
  exit 1
fi
# shellcheck disable=SC1091
source .deploy.env

: "${AKS_CLUSTER:?AKS_CLUSTER not set in .deploy.env}"
: "${RESOURCE_GROUP:?RESOURCE_GROUP not set in .deploy.env}"
: "${ACR_NAME:?ACR_NAME not set in .deploy.env}"
: "${OPENAI_ENDPOINT:?OPENAI_ENDPOINT not set in .deploy.env}"
: "${MODEL_DEPLOYMENT:?MODEL_DEPLOYMENT not set in .deploy.env}"
: "${GITHUB_ORG:?GITHUB_ORG not set in .deploy.env}"
: "${GITHUB_MODULES_REPO:?GITHUB_MODULES_REPO not set in .deploy.env}"

APP_NAME="snow-multi-agent"
IDENTITY_NAME="${APP_NAME}-identity"
IMAGE="${ACR_NAME}.azurecr.io/${APP_NAME}:latest"

# Derive OpenAI resource name from endpoint URL (https://my-hub.openai.azure.com/ → my-hub)
OPENAI_RESOURCE_NAME=$(echo "$OPENAI_ENDPOINT" | sed 's|https://||;s|\.openai\.azure\.com.*||')

echo ""
echo "=== snow-multi-agent-platform deploy ==="
echo "  Cluster:        $AKS_CLUSTER  ($RESOURCE_GROUP)"
echo "  ACR:            $ACR_NAME"
echo "  OpenAI:         $OPENAI_ENDPOINT  ($OPENAI_RESOURCE_NAME)"
echo "  Model:          $MODEL_DEPLOYMENT"
echo "  GitHub org:     $GITHUB_ORG / $GITHUB_MODULES_REPO"
echo "  Demo mode:      $( [[ -z "${GITHUB_PAT:-}" ]] && echo 'yes (no GitHub PAT)' || echo 'no' )"
echo ""

# ── Prerequisites check ───────────────────────────────────────────────────────
for cmd in az kubectl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd is not installed."
    exit 1
  fi
done

# ── Subscription and tenant ───────────────────────────────────────────────────
echo "[1/7] Getting subscription info..."
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
echo "      subscription: $SUBSCRIPTION_ID"
echo "      tenant:       $TENANT_ID"

# ── AKS credentials ───────────────────────────────────────────────────────────
echo "[2/7] Fetching AKS credentials..."
az aks get-credentials \
  --name "$AKS_CLUSTER" \
  --resource-group "$RESOURCE_GROUP" \
  --overwrite-existing

# ── Managed identity ─────────────────────────────────────────────────────────
echo "[3/7] Creating managed identity ($IDENTITY_NAME)..."
az identity create \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output none

IDENTITY_CLIENT_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query clientId -o tsv)

IDENTITY_OBJECT_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query principalId -o tsv)

echo "      client ID: $IDENTITY_CLIENT_ID"

# ── Workload identity federated credential ────────────────────────────────────
echo "[4/7] Wiring workload identity (AKS OIDC → managed identity)..."
OIDC_ISSUER=$(az aks show \
  --name "$AKS_CLUSTER" \
  --resource-group "$RESOURCE_GROUP" \
  --query "oidcIssuerProfile.issuerUrl" -o tsv)

if [[ -z "$OIDC_ISSUER" ]]; then
  echo "ERROR: OIDC issuer is empty — enable the OIDC issuer on your AKS cluster first:"
  echo "  az aks update --name $AKS_CLUSTER --resource-group $RESOURCE_GROUP --enable-oidc-issuer --enable-workload-identity"
  exit 1
fi

az identity federated-credential create \
  --name "${APP_NAME}-federated" \
  --identity-name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --issuer "$OIDC_ISSUER" \
  --subject "system:serviceaccount:default:${APP_NAME}" \
  --output none 2>/dev/null || echo "      (federated credential already exists — skipping)"

# ── Role assignment: Cognitive Services OpenAI User ──────────────────────────
echo "[5/7] Assigning Cognitive Services OpenAI User role..."
OPENAI_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${OPENAI_RESOURCE_NAME}"

az role assignment create \
  --role "Cognitive Services OpenAI User" \
  --assignee-object-id "$IDENTITY_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$OPENAI_RESOURCE_ID" \
  --output none 2>/dev/null || echo "      (role already assigned — skipping)"

# ── Build and push image ──────────────────────────────────────────────────────
echo "[6/7] Building and pushing image to ACR (this takes ~2 min)..."
az acr build \
  --registry "$ACR_NAME" \
  --image "${APP_NAME}:latest" \
  .

# ── Apply k8s manifests ───────────────────────────────────────────────────────
echo "[7/7] Applying Kubernetes manifests..."

DEMO_MODE="$( [[ -z "${GITHUB_PAT:-}" ]] && echo 'true' || echo 'false' )"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Substitute placeholders in manifests
for f in k8s/*.yaml; do
  sed \
    -e "s|<YOUR_ACR>|${ACR_NAME}|g" \
    -e "s|<AZURE_CLIENT_ID>|${IDENTITY_CLIENT_ID}|g" \
    -e "s|<AZURE_TENANT_ID>|${TENANT_ID}|g" \
    -e "s|<OPENAI_ENDPOINT>|${OPENAI_ENDPOINT}|g" \
    -e "s|<MODEL_DEPLOYMENT>|${MODEL_DEPLOYMENT}|g" \
    -e "s|\"natesanshreyas\"|\"${GITHUB_ORG}\"|g" \
    -e "s|\"terraform-modules-demo\"|\"${GITHUB_MODULES_REPO}\"|g" \
    -e "s|\"false\"|\"${DEMO_MODE}\"|g" \
    "$f" > "$TMPDIR/$(basename "$f")"
done

# Write secret (generated fresh — not from template)
cat > "$TMPDIR/secret.yaml" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${APP_NAME}-secrets
  labels:
    app: ${APP_NAME}
type: Opaque
stringData:
  GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PAT:-}"
EOF

# Apply in order
kubectl apply -f "$TMPDIR/serviceaccount.yaml"
kubectl apply -f "$TMPDIR/configmap.yaml"
kubectl apply -f "$TMPDIR/secret.yaml"
kubectl apply -f "$TMPDIR/deployment.yaml"
kubectl apply -f "$TMPDIR/service.yaml"
kubectl apply -f "$TMPDIR/ingress.yaml"

echo ""
echo "Waiting for rollout..."
kubectl rollout status deployment/"$APP_NAME" --timeout=120s

echo ""
echo "=== Deploy complete ==="
INGRESS_IP=$(kubectl get ingress "$APP_NAME" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "<pending>")
echo "  Ingress IP:  $INGRESS_IP"
echo "  Health:      curl http://$INGRESS_IP/health"
echo ""
