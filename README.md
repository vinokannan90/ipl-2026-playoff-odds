# IPL 2026 — Playoff Probability Simulator

Live IPL 2026 playoff odds via client-side Monte Carlo, with an optional
LLM agent that derives per-match priors and answers natural-language questions.

- **Frontend:** vanilla HTML/JS on Azure Static Web Apps (free tier)
- **Backend:** FastAPI on Azure Container Apps with **scale-to-zero**
- **LLM:** GitHub Models (free with Copilot Pro), swappable to Azure OpenAI
- **Cache:** Azure Blob Storage
- **Secrets:** Azure Key Vault, accessed via Managed Identity (no keys in env or repo)
- **CI/CD:** GitHub Actions with OIDC federated credentials (no long-lived secrets)
- **Observability:** Log Analytics + Application Insights

**Idle cost:** ~$0/month. You only pay when the API serves requests, the daily job runs, or you exceed the SWA free tier.

---

## Repo layout

```
frontend/         Static Web App (HTML/JS/CSS, modular)
backend/          FastAPI + agents + Dockerfile
infra/            Bicep IaC
.github/workflows CI/CD pipelines
azure.yaml        azd config
```

---

## One-time setup

### 1. Tools

```pwsh
winget install Microsoft.AzureCLI
winget install Microsoft.Azd
winget install Docker.DockerDesktop
winget install Python.Python.3.12
```

### 2. Get a GitHub Models token (free with Copilot Pro)

Go to https://github.com/settings/tokens → "Generate new token (fine-grained)". No special scopes needed for GitHub Models.

### 3. Create the Azure resources

```pwsh
az login
azd auth login

# From the repo root
azd env new prod
azd env set AZURE_LOCATION centralindia
azd env set AZURE_NAME_PREFIX iplodds
azd env set AZURE_SUBSCRIPTION_ID dc958fdf-4614-4532-b9d0-9627780aad94

azd up
```

`azd up` provisions everything in `rg-iplodds-prod` and deploys the backend image.

### 4. Store the GitHub Models token in Key Vault

```pwsh
$kv = (azd env get-values | Select-String '^KEY_VAULT_NAME').ToString().Split('=')[1].Trim('"')
az keyvault secret set --vault-name $kv --name github-models-token --value <YOUR_TOKEN>
```

### 5. Wire GitHub Actions OIDC (one time, no long-lived secrets)

> **Shortcut:** the steps in §4 and §5 are scripted end-to-end in
> [`scripts/bootstrap.ps1`](./scripts/bootstrap.ps1). Run it once and skip the
> manual `az ad app create` + `az role assignment` + `gh variable set` chain:
>
> ```pwsh
> # Full bootstrap (preflight + OIDC + RBAC + KV secret + GH variables)
> ./scripts/bootstrap.ps1
>
> # Or run a single phase, e.g. only set GH Actions variables after `azd up`:
> ./scripts/bootstrap.ps1 -Phase ghvars
> ```
>
> The script is idempotent. It auto-detects the GitHub repo from `git remote`,
> creates the app registration, adds federated credentials for `main`, PRs, and
> the `prod` environment, grants `Contributor` + `RBAC Administrator` on the
> resource group, writes the Models token to Key Vault, and pushes
> `AZURE_*` variables and the SWA deployment token to the repo via `gh`.

The manual equivalent (for reference):

```pwsh
# Create app registration with federated credential for this repo
$appName = "iplodds-gha"
$repo    = "<your-gh-user>/ipl-2026-playoff-odds"
$appId   = az ad app create --display-name $appName --query appId -o tsv
az ad sp create --id $appId | Out-Null

# Federated credential bound to main branch
az ad app federated-credential create --id $appId --parameters @{
  name        = 'main'
  issuer      = 'https://token.actions.githubusercontent.com'
  subject     = "repo:$repo:ref:refs/heads/main"
  audiences   = @('api://AzureADTokenExchange')
} | ConvertTo-Json | Out-File fc.json
az ad app federated-credential create --id $appId --parameters @fc.json
Remove-Item fc.json

# Grant Contributor on the resource group
$sub = az account show --query id -o tsv
az role assignment create --assignee $appId --role Contributor `
  --scope "/subscriptions/$sub/resourceGroups/rg-iplodds-prod"
```

Then in your GitHub repo → Settings → Secrets and variables → Actions → **Variables**:

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | output `appId` from above |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `dc958fdf-4614-4532-b9d0-9627780aad94` |
| `AZURE_RG` | `rg-iplodds-prod` |
| `AZURE_CONTAINER_APP` | from `azd env get-values` |
| `AZURE_CONTAINER_ENV` | from `azd env get-values` |
| `BACKEND_URL` | from `azd env get-values` (no protocol/host of backend) |

Add this **secret** (one-time, from SWA portal → Manage deployment token):

| Secret | Source |
|---|---|
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | SWA → Overview → Manage deployment token |

### 6. Custom domain `playoffodds.ai`

Register the domain at any registrar (this project uses **Cloudflare Registrar**,
which gives you free DNS + CNAME-flattening on the apex out of the box).

#### a. DNS records (Cloudflare → DNS → Records)

| Type | Name | Content | Proxy |
|---|---|---|---|
| `CNAME` | `@` | `<swa-name>.azurestaticapps.net` | DNS only (grey cloud) |
| `CNAME` | `www` | `<swa-name>.azurestaticapps.net` | DNS only (grey cloud) |

Cloudflare auto-flattens the apex `CNAME` to an A record at query time, so
public resolvers see an A for `playoffodds.ai` and a real CNAME for `www`.

#### b. Bind the sub-domain (CNAME validation, default)

```pwsh
az staticwebapp hostname set -n <swa-name> -g rg-iplodds-prod `
  --hostname www.playoffodds.ai --no-wait
```

#### c. Bind the apex (TXT-token validation, required)

Azure SWA refuses CNAME validation on apex domains, so the root must use a
TXT token:

```pwsh
# 1. Request the binding (generates a token after a few seconds)
az staticwebapp hostname set -n <swa-name> -g rg-iplodds-prod `
  --hostname playoffodds.ai --validation-method dns-txt-token

# 2. Read the token
az staticwebapp hostname show -n <swa-name> -g rg-iplodds-prod `
  --hostname playoffodds.ai --query "validationToken" -o tsv
```

Add the token as a TXT record in Cloudflare:

| Type | Name | Content | TTL |
|---|---|---|---|
| `TXT` | `@` | `<validationToken from step 2>` | Auto |

Azure polls public DNS and flips the hostname to `Ready` within a few minutes;
re-check with:

```pwsh
az staticwebapp hostname list -n <swa-name> -g rg-iplodds-prod -o table
```

TLS certs are issued automatically once both hostnames are `Ready`.

---

## Local development

### Backend

```pwsh
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
# Fill in IPLODDS_GITHUB_MODELS_TOKEN
uvicorn iplodds.main:app --reload
```

API at http://localhost:8000/docs.

### Frontend

```pwsh
cd frontend
python -m http.server 5173
```

Open http://localhost:5173 — it'll talk directly to iplt20 (no backend needed) until you set:

```html
<!-- before <script src="./js/app.js"> -->
<script>window.__API_BASE__ = "http://localhost:8000";</script>
```

### Tests

```pwsh
cd backend
pytest
ruff check .
```

---

## Security posture

- **No secrets in code or env files.** `IPLODDS_GITHUB_MODELS_TOKEN` is a Key Vault reference at runtime.
- **Managed Identity** on Container App + Job; no service principals with passwords.
- **Storage:** RBAC-only (`allowSharedKeyAccess: false`), HTTPS only, TLS 1.2+, no public blob access.
- **Key Vault:** RBAC-only, soft-delete + purge protection on.
- **Container:** non-root UID 10001, distroless-style slim base, no `pip` at runtime.
- **API:** strict CORS allowlist, rate limiting (`30/min` default, `10/min` for agent), 16 KB body cap, security headers, request-id propagation.
- **Frontend:** strict CSP, `X-Frame-Options: DENY`, HSTS preload, no inline scripts.
- **Pydantic** validates every input. **slowapi** prevents flood. **tenacity** caps upstream retries.
- **CI:** SBOM + provenance on container builds. OIDC for deploys (no PATs).
- **LLM:** prompts forbid speculation about injuries/news; outputs are clamped + sanitized; no user input is passed to a tool that has side effects (all tools are read-only).

---

## Cost expectations (Central India, public list prices, May 2026)

| Component | Idle | At 1k requests/day | At 100k requests/day |
|---|---|---|---|
| Static Web App (Free tier) | $0 | $0 | $0 (100 GB/mo bandwidth) |
| Container App (scale-to-zero, 0.5 vCPU / 1 GiB) | $0 | <$1 | ~$15-30 |
| Storage (Blob, <1 GB) | <$0.10 | <$0.10 | <$1 |
| Key Vault (standard) | <$0.05/10k ops | <$0.10 | <$1 |
| Log Analytics (1 GB cap) | <$2 | <$2 | <$5 |
| GitHub Models | Free | Free | Free (rate-limited) |
| Container Apps Job (1 run/day, ~30s) | <$0.05 | <$0.05 | <$0.05 |
| **Total** | **<$3/mo** | **<$5/mo** | **~$25-40/mo** |

To stay free in early days: keep the Log Analytics daily cap at 1 GB (already set), set min replicas to 0 (currently 1 for warm starts — flip to 0 to save ~$5-8/mo at the cost of ~3-5 s cold-start latency on the first request after idle), and don't enable scout until you have a paid news source.

---

## Roadmap

- [x] M1 — Project structure, bug fixes, deploy skeleton, security baseline
- [x] M1 — LLM-derived match priors agent
- [x] M1 — Tool-calling Q&A agent
- [x] M1 — Daily auto-update job (Container Apps Job, cron)
- [ ] M2 — Full leverage scoring (highest-impact remaining matches)
- [ ] M2 — News/injury scout (needs a vetted RSS or paid news API)
- [ ] M2 — OpenGraph share-card auto-generation per team
- [ ] M2 — Playwright E2E + Lighthouse CI
- [ ] M3 — Historical backtesting (compare prior calibration vs actuals)

---

## License

MIT.
