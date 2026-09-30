// =====================================================================================
// Edge IQ - Water Utility Edge Intelligence Accelerator
// Main deployment orchestration. Mirrors the Microsoft IQ Solution Orchestrator layout:
//   modules/ai        -> Foundry (project, models, connections), AI Search  [Foundry IQ]
//   modules/data      -> Cosmos DB, Storage, Event Hub, IoT Hub, App Configuration
//   modules/fabric    -> Fabric capacity (Lakehouse + OneLake + Eventhouse host) [Fabric IQ]
//   modules/compute   -> Container Apps env, API + Web + MCP server apps
//   modules/identity  -> user-assigned managed identity + RBAC
//   modules/monitoring-> Log Analytics + Application Insights
//   modules/security  -> Key Vault
// =====================================================================================
targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// CORE PARAMETERS - change these to retarget dev / prod
// ---------------------------------------------------------------------------
@minLength(3)
@maxLength(16)
@description('Short name used to derive all resource names. e.g. edgeiq')
param solutionName string = 'edgeiq'

@allowed(['dev', 'test', 'prod'])
@description('Deployment environment. Drives SKU sizing defaults.')
param environmentName string = 'dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Tags applied to every resource.')
param tags object = {
  solution: 'edge-iq'
  scenario: 'water-utility-edge-intelligence'
  environment: environmentName
}

// ---------------------------------------------------------------------------
// FOUNDRY IQ PARAMETERS
// ---------------------------------------------------------------------------
@description('Foundry chat/reasoning model deployment name.')
param chatModelName string = 'gpt-4o'

@description('Foundry chat model version.')
param chatModelVersion string = '2024-11-20'

@description('Chat model capacity in thousands of TPM.')
param chatModelCapacity int = 50

@description('Embedding model used for the Foundry IQ knowledge index.')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version.')
param embeddingModelVersion string = '1'

@description('Embedding model capacity in thousands of TPM.')
param embeddingModelCapacity int = 50

@description('Existing Foundry project resource ID. Leave empty to create a new one.')
param existingFoundryProjectResourceId string = ''

// ---------------------------------------------------------------------------
// FABRIC IQ PARAMETERS
// ---------------------------------------------------------------------------
@description('Deploy a Microsoft Fabric capacity. Set false if reusing an existing capacity.')
param deployFabricCapacity bool = true

@description('Fabric capacity SKU. F2 is the smallest; use F64 for production.')
@allowed(['F2', 'F4', 'F8', 'F16', 'F32', 'F64', 'F128'])
param fabricCapacitySku string = 'F2'

@description('Entra object IDs (users or groups) that administer the Fabric capacity.')
param fabricAdminMembers array = []

@description('Name of the Fabric workspace that hosts the Edge IQ Lakehouse. Created post-provision.')
param fabricWorkspaceName string = 'EdgeIQ-WaterUtility'

@description('Name of the Fabric Lakehouse in OneLake.')
param fabricLakehouseName string = 'EdgeIQ_Lakehouse'

@description('Name of the Fabric Eventhouse/KQL database for edge telemetry.')
param fabricEventhouseName string = 'EdgeIQ_Telemetry'

// ---------------------------------------------------------------------------
// WORK IQ / M365 PARAMETERS (placeholders wired into App Configuration)
// ---------------------------------------------------------------------------
@description('Entra tenant ID used for Work IQ / Microsoft Graph grounding.')
param m365TenantId string = tenant().tenantId

@description('Entra app (client) ID with Graph permissions for Work IQ connectors.')
param m365ClientId string = ''

@description('SharePoint site URLs providing Work IQ document grounding.')
param m365SharePointSiteUrls array = []

@description('Teams channel IDs monitored for operations chatter grounding.')
param m365TeamsChannelIds array = []

@description('Copilot Studio environment ID hosting the Edge IQ front-door agent.')
param copilotStudioEnvironmentId string = ''

@description('Copilot Studio agent (bot) schema name.')
param copilotStudioAgentSchemaName string = 'edgeiq_water_edge_agent'

// ---------------------------------------------------------------------------
// EDGE / IOT PARAMETERS
// ---------------------------------------------------------------------------
@description('IoT Hub SKU.')
@allowed(['F1', 'S1', 'S2', 'S3'])
param iotHubSku string = 'S1'

@description('IoT Hub units.')
param iotHubCapacity int = 1

@description('Event Hub throughput units for the telemetry hot path.')
param eventHubCapacity int = 1

// ---------------------------------------------------------------------------
// DATA PARAMETERS
// ---------------------------------------------------------------------------
@description('Enable Cosmos DB serverless. Set false for provisioned throughput in prod.')
param cosmosServerless bool = true

@description('Provisioned RU/s per Cosmos container when serverless is disabled.')
param cosmosThroughput int = 1000

@description('AI Search SKU.')
@allowed(['basic', 'standard', 'standard2'])
param searchSku string = 'basic'

// ---------------------------------------------------------------------------
// COMPUTE PARAMETERS
// ---------------------------------------------------------------------------
@description('Container image tag deployed to all apps.')
param imageTag string = 'latest'

@description('Minimum replicas per container app.')
param minReplicas int = 0

@description('Maximum replicas per container app.')
param maxReplicas int = 3

@description('Principal ID of the deploying user, granted data-plane roles.')
param deployerPrincipalId string = ''

// ---------------------------------------------------------------------------
// NAMING
// ---------------------------------------------------------------------------
var uniqueSuffix = substring(uniqueString(resourceGroup().id, solutionName), 0, 6)
var namePrefix = '${solutionName}-${environmentName}'
var names = {
  identity: 'id-${namePrefix}-${uniqueSuffix}'
  logAnalytics: 'log-${namePrefix}-${uniqueSuffix}'
  appInsights: 'appi-${namePrefix}-${uniqueSuffix}'
  keyVault: take('kv${solutionName}${environmentName}${uniqueSuffix}', 24)
  storage: take(toLower('st${solutionName}${environmentName}${uniqueSuffix}'), 24)
  cosmos: 'cosmos-${namePrefix}-${uniqueSuffix}'
  search: 'srch-${namePrefix}-${uniqueSuffix}'
  foundry: 'aif-${namePrefix}-${uniqueSuffix}'
  foundryProject: 'proj-${namePrefix}'
  iotHub: 'iot-${namePrefix}-${uniqueSuffix}'
  eventHubNs: 'evhns-${namePrefix}-${uniqueSuffix}'
  fabric: take(toLower('fab${solutionName}${environmentName}${uniqueSuffix}'), 24)
  acaEnv: 'cae-${namePrefix}-${uniqueSuffix}'
  acr: take(toLower('acr${solutionName}${environmentName}${uniqueSuffix}'), 50)
  appConfig: 'appcs-${namePrefix}-${uniqueSuffix}'
}

var dataPlanePrincipals = union([identity.outputs.principalId], empty(deployerPrincipalId) ? [] : [deployerPrincipalId])

// ---------------------------------------------------------------------------
// MODULES
// ---------------------------------------------------------------------------
module monitoring 'bicep/modules/monitoring/monitoring.bicep' = {
  name: 'deploy-monitoring'
  params: {
    logAnalyticsName: names.logAnalytics
    appInsightsName: names.appInsights
    location: location
    tags: tags
  }
}

module identity 'bicep/modules/identity/managed-identity.bicep' = {
  name: 'deploy-identity'
  params: {
    identityName: names.identity
    location: location
    tags: tags
  }
}

module security 'bicep/modules/security/key-vault.bicep' = {
  name: 'deploy-keyvault'
  params: {
    keyVaultName: names.keyVault
    location: location
    tags: tags
    principalIds: dataPlanePrincipals
  }
}

module storage 'bicep/modules/data/storage-account.bicep' = {
  name: 'deploy-storage'
  params: {
    storageAccountName: names.storage
    location: location
    tags: tags
    principalIds: dataPlanePrincipals
  }
}

module cosmos 'bicep/modules/data/cosmos-db-nosql.bicep' = {
  name: 'deploy-cosmos'
  params: {
    accountName: names.cosmos
    location: location
    tags: tags
    serverless: cosmosServerless
    throughput: cosmosThroughput
    principalIds: dataPlanePrincipals
  }
}

module eventHub 'bicep/modules/data/event-hub.bicep' = {
  name: 'deploy-eventhub'
  params: {
    namespaceName: names.eventHubNs
    location: location
    tags: tags
    capacity: eventHubCapacity
    principalIds: dataPlanePrincipals
  }
}

module iotHub 'bicep/modules/data/iot-hub.bicep' = {
  name: 'deploy-iothub'
  params: {
    iotHubName: names.iotHub
    location: location
    tags: tags
    sku: iotHubSku
    capacity: iotHubCapacity
    telemetryEventHubName: eventHub.outputs.telemetryHubName
    telemetryAuthRuleId: eventHub.outputs.sendAuthorizationRuleId
    principalIds: dataPlanePrincipals
  }
}

module search 'bicep/modules/ai/ai-search.bicep' = {
  name: 'deploy-search'
  params: {
    searchServiceName: names.search
    location: location
    tags: tags
    sku: searchSku
    principalIds: dataPlanePrincipals
  }
}

module foundry 'bicep/modules/ai/ai-foundry.bicep' = {
  name: 'deploy-foundry'
  params: {
    accountName: names.foundry
    projectName: names.foundryProject
    location: location
    tags: tags
    existingProjectResourceId: existingFoundryProjectResourceId
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatModelCapacity: chatModelCapacity
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingModelCapacity: embeddingModelCapacity
    searchServiceResourceId: search.outputs.resourceId
    searchServiceEndpoint: search.outputs.endpoint
    cosmosAccountResourceId: cosmos.outputs.resourceId
    cosmosEndpoint: cosmos.outputs.endpoint
    storageAccountResourceId: storage.outputs.resourceId
    appInsightsResourceId: monitoring.outputs.appInsightsId
    principalIds: dataPlanePrincipals
  }
}

module fabric 'bicep/modules/fabric/fabric-capacity.bicep' = if (deployFabricCapacity) {
  name: 'deploy-fabric'
  params: {
    capacityName: names.fabric
    location: location
    tags: tags
    sku: fabricCapacitySku
    adminMembers: fabricAdminMembers
  }
}

module appConfig 'bicep/modules/data/app-configuration.bicep' = {
  name: 'deploy-appconfig'
  params: {
    configStoreName: names.appConfig
    location: location
    tags: tags
    principalIds: dataPlanePrincipals
    keyValues: [
      { key: 'EdgeIQ:FabricIQ:WorkspaceName', value: fabricWorkspaceName }
      { key: 'EdgeIQ:FabricIQ:LakehouseName', value: fabricLakehouseName }
      { key: 'EdgeIQ:FabricIQ:EventhouseName', value: fabricEventhouseName }
      { key: 'EdgeIQ:WorkIQ:TenantId', value: m365TenantId }
      { key: 'EdgeIQ:WorkIQ:ClientId', value: m365ClientId }
      { key: 'EdgeIQ:WorkIQ:SharePointSiteUrls', value: join(m365SharePointSiteUrls, ',') }
      { key: 'EdgeIQ:WorkIQ:TeamsChannelIds', value: join(m365TeamsChannelIds, ',') }
      { key: 'EdgeIQ:CopilotStudio:EnvironmentId', value: copilotStudioEnvironmentId }
      { key: 'EdgeIQ:CopilotStudio:AgentSchemaName', value: copilotStudioAgentSchemaName }
    ]
  }
}

module compute 'bicep/modules/compute/container-apps.bicep' = {
  name: 'deploy-compute'
  params: {
    environmentAppName: names.acaEnv
    containerRegistryName: names.acr
    location: location
    tags: tags
    imageTag: imageTag
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    managedIdentityResourceId: identity.outputs.resourceId
    managedIdentityClientId: identity.outputs.clientId
    managedIdentityPrincipalId: identity.outputs.principalId
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: monitoring.outputs.logAnalyticsSharedKey
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    settings: {
      AZURE_AI_AGENT_ENDPOINT: foundry.outputs.projectEndpoint
      AZURE_AI_FOUNDRY_ACCOUNT: foundry.outputs.accountName
      AZURE_OPENAI_CHAT_DEPLOYMENT: chatModelName
      AZURE_OPENAI_EMBEDDING_DEPLOYMENT: embeddingModelName
      AZURE_SEARCH_ENDPOINT: search.outputs.endpoint
      AZURE_SEARCH_INDEX: 'edgeiq-knowledge'
      AZURE_COSMOS_ENDPOINT: cosmos.outputs.endpoint
      AZURE_COSMOS_DATABASE: cosmos.outputs.databaseName
      AZURE_STORAGE_ACCOUNT: storage.outputs.name
      AZURE_EVENTHUB_NAMESPACE: eventHub.outputs.namespaceFqdn
      AZURE_EVENTHUB_TELEMETRY: eventHub.outputs.telemetryHubName
      AZURE_IOTHUB_NAME: iotHub.outputs.name
      AZURE_IOTHUB_HOSTNAME: iotHub.outputs.hostName
      AZURE_APPCONFIG_ENDPOINT: appConfig.outputs.endpoint
      AZURE_KEY_VAULT_ENDPOINT: security.outputs.endpoint
      FABRIC_WORKSPACE_NAME: fabricWorkspaceName
      FABRIC_LAKEHOUSE_NAME: fabricLakehouseName
      FABRIC_EVENTHOUSE_NAME: fabricEventhouseName
      M365_TENANT_ID: m365TenantId
      M365_CLIENT_ID: m365ClientId
      COPILOT_STUDIO_ENVIRONMENT_ID: copilotStudioEnvironmentId
      COPILOT_STUDIO_AGENT_SCHEMA_NAME: copilotStudioAgentSchemaName
    }
  }
}

// ---------------------------------------------------------------------------
// OUTPUTS - consumed by azd, seed scripts and agent registration
// ---------------------------------------------------------------------------
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_TENANT_ID string = tenant().tenantId

output AZURE_AI_AGENT_ENDPOINT string = foundry.outputs.projectEndpoint
output AZURE_AI_FOUNDRY_ACCOUNT string = foundry.outputs.accountName
output AZURE_AI_FOUNDRY_PROJECT string = foundry.outputs.projectName
output AZURE_OPENAI_CHAT_DEPLOYMENT string = chatModelName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = embeddingModelName

output AZURE_SEARCH_ENDPOINT string = search.outputs.endpoint
output AZURE_SEARCH_SERVICE_NAME string = search.outputs.name

output AZURE_COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output AZURE_COSMOS_ACCOUNT_NAME string = cosmos.outputs.name
output AZURE_COSMOS_DATABASE string = cosmos.outputs.databaseName

output AZURE_STORAGE_ACCOUNT string = storage.outputs.name
output AZURE_STORAGE_BLOB_ENDPOINT string = storage.outputs.blobEndpoint

output AZURE_EVENTHUB_NAMESPACE string = eventHub.outputs.namespaceFqdn
output AZURE_EVENTHUB_TELEMETRY string = eventHub.outputs.telemetryHubName

output AZURE_IOTHUB_NAME string = iotHub.outputs.name
output AZURE_IOTHUB_HOSTNAME string = iotHub.outputs.hostName

output AZURE_APPCONFIG_ENDPOINT string = appConfig.outputs.endpoint
output AZURE_KEY_VAULT_ENDPOINT string = security.outputs.endpoint

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = compute.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = compute.outputs.registryName
output AZURE_CONTAINER_APPS_ENVIRONMENT string = compute.outputs.environmentName
output WEB_APP_URL string = compute.outputs.webAppUrl
output API_APP_URL string = compute.outputs.apiAppUrl
output MCP_GATEWAY_URL string = compute.outputs.mcpGatewayUrl

output AZURE_CLIENT_ID string = identity.outputs.clientId
output AZURE_MANAGED_IDENTITY_RESOURCE_ID string = identity.outputs.resourceId

output FABRIC_CAPACITY_NAME string = deployFabricCapacity ? fabric!.outputs.name : ''
output FABRIC_WORKSPACE_NAME string = fabricWorkspaceName
output FABRIC_LAKEHOUSE_NAME string = fabricLakehouseName
output FABRIC_EVENTHOUSE_NAME string = fabricEventhouseName

output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
