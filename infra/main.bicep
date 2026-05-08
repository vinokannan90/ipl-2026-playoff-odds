// Main Bicep deployment for IPL 2026 playoff odds.
// Resources:
//   - Log Analytics + Application Insights (observability)
//   - Storage Account (Blob cache)
//   - Key Vault (secrets, RBAC mode)
//   - User-Assigned Managed Identity (used by Container App + Job)
//   - Container Apps Environment (consumption, scale-to-zero capable)
//   - Container App (FastAPI service, min replicas 0)
//   - Container Apps Job (scheduled daily update)
//   - Static Web App (frontend)
//
// Idle cost: ~$0/mo. Charges accrue only when API receives requests or job runs.

targetScope = 'resourceGroup'

@description('Short prefix used for resource naming. Lowercase alphanumeric.')
@minLength(3)
@maxLength(12)
param namePrefix string

@description('Azure region for all resources. Static Web Apps are deployed globally.')
param location string = resourceGroup().location

@description('Container image for the backend (e.g. ghcr.io/<owner>/iplodds-backend:sha-abc).')
param backendImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('GitHub fine-grained PAT for GitHub Models API. Set once via: azd env set GITHUB_MODELS_TOKEN <token>')
@secure()
param githubModelsToken string = ''

@description('Tags applied to every resource.')
param tags object = {
  app: 'iplodds'
  env: 'prod'
  managedBy: 'azd'
}

@description('Custom hostnames to bind to the Static Web App. Apex domains must already have a TXT validation record published (see README §6c).')
param customDomains array = [
  { name: 'playoffodds.ai', validationMethod: 'dns-txt-token' }
  { name: 'www.playoffodds.ai', validationMethod: 'cname-delegation' }
]

var uniq = uniqueString(resourceGroup().id, namePrefix)
var saName = toLower('${namePrefix}st${take(uniq, 6)}')
var kvName = toLower('${namePrefix}-kv-${take(uniq, 6)}')
var acrName = toLower('${namePrefix}acr${take(uniq, 6)}')
var laName = '${namePrefix}-log'
var aiName = '${namePrefix}-appi'
var miName = '${namePrefix}-mi'
var caEnvName = '${namePrefix}-cae'
var caName = '${namePrefix}-api'
var jobName = '${namePrefix}-daily'
var swaName = '${namePrefix}-web'
var customDomainOrigins = [for d in customDomains: 'https://${d.name}']
var allCorsOrigins = union([ 'https://${swa.properties.defaultHostname}' ], customDomainOrigins)

// ---------------- Observability ----------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: laName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: aiName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------- Identity ----------------
resource mi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: miName
  location: location
  tags: tags
}

// ---------------- Storage (Blob cache) ----------------
resource sa 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: saName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false  // RBAC only — no account keys
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
    encryption: {
      services: { blob: { enabled: true }, file: { enabled: true } }
      keySource: 'Microsoft.Storage'
    }
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: sa
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource cacheContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'cache'
  properties: { publicAccess: 'None' }
}

// Storage Blob Data Contributor → MI
resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sa.id, mi.id, 'blob-contributor')
  scope: sa
  properties: {
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

// ---------------- Key Vault ----------------
resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

// Key Vault Secrets User → MI (read secrets only)
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, mi.id, 'kv-secrets-user')
  scope: kv
  properties: {
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

// Secret slot for GitHub Models PAT.
// Value comes from the 'githubModelsToken' parameter (set via: azd env set GITHUB_MODELS_TOKEN <token>).
// If the parameter is empty (e.g. first-time deploy without setting it), a placeholder is written
// and the agent will be unavailable until the real value is stored.
resource githubTokenSecret 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'github-models-token'
  properties: { value: empty(githubModelsToken) ? 'set-me-via-cli' : githubModelsToken, attributes: { enabled: true } }
}

// ---------------- Container Registry ----------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
  }
}

// AcrPull → MI (so Container App can pull images)
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, mi.id, 'acr-pull')
  scope: acr
  properties: {
    principalId: mi.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

// ---------------- Container Apps Environment ----------------
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    zoneRedundant: false
  }
}

// ---------------- Container App (API) ----------------
resource ca 'Microsoft.App/containerApps@2024-03-01' = {
  name: caName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${mi.id}': {} }
  }
  properties: {
    environmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          identity: mi.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [ { latestRevision: true, weight: 100 } ]
        corsPolicy: {
          allowedOrigins: allCorsOrigins
          allowedMethods: [ 'GET', 'POST', 'OPTIONS' ]
          allowedHeaders: [ 'Content-Type' ]
          maxAge: 600
          allowCredentials: false
        }
      }
      secrets: [
        {
          name: 'github-models-token'
          keyVaultUrl: '${kv.properties.vaultUri}secrets/github-models-token'
          identity: mi.id
        }
        {
          name: 'cricketdata-api-key'
          keyVaultUrl: '${kv.properties.vaultUri}secrets/cricketdata-api-key'
          identity: mi.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: backendImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'IPLODDS_ENV', value: 'prod' }
            { name: 'IPLODDS_LOG_LEVEL', value: 'INFO' }
            { name: 'IPLODDS_CORS_ORIGINS', value: join(allCorsOrigins, ',') }
            { name: 'IPLODDS_BLOB_ACCOUNT_URL', value: 'https://${sa.name}.blob.${environment().suffixes.storage}' }
            { name: 'IPLODDS_LLM_PROVIDER', value: 'github' }
            { name: 'IPLODDS_GITHUB_MODELS_TOKEN', secretRef: 'github-models-token' }
            { name: 'IPLODDS_CRICKETDATA_API_KEY', secretRef: 'cricketdata-api-key' }
            { name: 'AZURE_CLIENT_ID', value: mi.properties.clientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
          ]
          probes: [
            { type: 'Liveness',  httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 5, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, initialDelaySeconds: 2, periodSeconds: 10 }
          ]
        }
      ]
      scale: {
        minReplicas: 1          // keep one warm to avoid cold starts
        maxReplicas: 10
        rules: [
          {
            name: 'http-rule'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

// ---------------- Container Apps Job (daily update) ----------------
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${mi.id}': {} }
  }
  properties: {
    environmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 600
      replicaRetryLimit: 1
      registries: [
        {
          server: acr.properties.loginServer
          identity: mi.id
        }
      ]
      scheduleTriggerConfig: {
        cronExpression: '0 4 * * *'  // 04:00 UTC = 09:30 IST
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        {
          name: 'github-models-token'
          keyVaultUrl: '${kv.properties.vaultUri}secrets/github-models-token'
          identity: mi.id
        }
        {
          name: 'cricketdata-api-key'
          keyVaultUrl: '${kv.properties.vaultUri}secrets/cricketdata-api-key'
          identity: mi.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'job'
          image: backendImage
          command: [ 'python', '-m', 'iplodds.jobs.daily_update' ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'IPLODDS_ENV', value: 'prod' }
            { name: 'IPLODDS_BLOB_ACCOUNT_URL', value: 'https://${sa.name}.blob.${environment().suffixes.storage}' }
            { name: 'IPLODDS_LLM_PROVIDER', value: 'github' }
            { name: 'IPLODDS_GITHUB_MODELS_TOKEN', secretRef: 'github-models-token' }
            { name: 'IPLODDS_CRICKETDATA_API_KEY', secretRef: 'cricketdata-api-key' }
            { name: 'AZURE_CLIENT_ID', value: mi.properties.clientId }
          ]
        }
      ]
    }
  }
}

// ---------------- Static Web App ----------------
resource swa 'Microsoft.Web/staticSites@2023-12-01' = {
  name: swaName
  location: 'centralus'  // SWA Free tier regions are limited; centralus works globally
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {
    // No repository wiring here -- frontend is deployed via GitHub Actions
    // using the SWA deployment token (see .github/workflows/frontend.yml).
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
  }
}

// Bind custom hostnames. For apex domains use 'dns-txt-token' and ensure the
// TXT validation record is published in DNS BEFORE deploying, otherwise the
// deployment will fail validation. Sub-domains use 'cname-delegation' and only
// need the CNAME record to exist.
resource swaDomains 'Microsoft.Web/staticSites/customDomains@2023-12-01' = [for d in customDomains: {
  parent: swa
  name: d.name
  properties: {
    validationMethod: d.validationMethod
  }
}]

// ---------------- Outputs ----------------
output backendUrl string = 'https://${ca.properties.configuration.ingress.fqdn}'
output frontendUrl string = 'https://${swa.properties.defaultHostname}'
output keyVaultUri string = kv.properties.vaultUri
output storageAccount string = sa.name
output managedIdentityClientId string = mi.properties.clientId
output appInsightsConnectionString string = appi.properties.ConnectionString
output staticWebAppName string = swa.name
output containerAppName string = ca.name
output containerEnvName string = cae.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
