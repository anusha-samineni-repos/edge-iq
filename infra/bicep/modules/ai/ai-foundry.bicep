// =====================================================================================
// Microsoft Foundry (AI Foundry) account + project - the Foundry IQ layer.
// Hosts the Edge IQ Solution Orchestrator agent and all specialist agents, plus
// project connections to AI Search (knowledge), Cosmos DB (memory) and Storage.
// =====================================================================================
@description('Foundry (Cognitive Services AIServices) account name.')
param accountName string

@description('Foundry project name.')
param projectName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('If set, an existing Foundry project resource ID is reused instead of creating one.')
param existingProjectResourceId string = ''

@description('Chat/reasoning model deployment name.')
param chatModelName string

@description('Chat model version.')
param chatModelVersion string

@description('Chat model capacity (thousand TPM).')
param chatModelCapacity int

@description('Embedding model deployment name.')
param embeddingModelName string

@description('Embedding model version.')
param embeddingModelVersion string

@description('Embedding model capacity (thousand TPM).')
param embeddingModelCapacity int

@description('AI Search resource ID for the knowledge connection.')
param searchServiceResourceId string

@description('AI Search endpoint.')
param searchServiceEndpoint string

@description('Cosmos DB resource ID for the agent memory connection.')
param cosmosAccountResourceId string

@description('Cosmos DB endpoint.')
param cosmosEndpoint string

@description('Storage account resource ID for the file/document connection.')
param storageAccountResourceId string

@description('Application Insights resource ID for agent tracing.')
param appInsightsResourceId string

@description('Principal IDs granted Azure AI Developer + Cognitive Services OpenAI User.')
param principalIds array = []

var azureAIDeveloper = '64702f94-c441-49e6-a78b-ef80e0188fee'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var createProject = empty(existingProjectResourceId)

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: { defaultAction: 'Allow' }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: chatModelName
  sku: { name: 'GlobalStandard', capacity: chatModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: chatModelName, version: chatModelVersion }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: embeddingModelName
  sku: { name: 'Standard', capacity: embeddingModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: embeddingModelName, version: embeddingModelVersion }
  }
  dependsOn: [chatDeployment]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = if (createProject) {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Edge IQ - Water Utility'
    description: 'Solution Orchestrator project for water utility IoT Edge intelligence.'
  }
}

// ---- Project connections (Foundry IQ knowledge + memory + telemetry sources) ----
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (createProject) {
  parent: project
  name: 'edgeiq-knowledge-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchServiceEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchServiceResourceId
      location: location
    }
  }
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (createProject) {
  parent: project
  name: 'edgeiq-agent-memory'
  properties: {
    category: 'CosmosDB'
    target: cosmosEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmosAccountResourceId
      location: location
    }
  }
  dependsOn: [searchConnection]
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (createProject) {
  parent: project
  name: 'edgeiq-documents'
  properties: {
    category: 'AzureStorageAccount'
    target: storageAccountResourceId
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: storageAccountResourceId
      location: location
    }
  }
  dependsOn: [cosmosConnection]
}

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (createProject) {
  parent: project
  name: 'edgeiq-observability'
  properties: {
    category: 'AppInsights'
    target: appInsightsResourceId
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsResourceId
    }
  }
  dependsOn: [storageConnection]
}

resource aiDevRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(account.id, pid, azureAIDeveloper)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIDeveloper)
    principalId: pid
  }
}]

resource openAIRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(account.id, pid, cognitiveServicesOpenAIUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
    principalId: pid
  }
}]

output accountResourceId string = account.id
output accountName string = account.name
output accountEndpoint string = account.properties.endpoint
output projectName string = createProject ? project.name : last(split(existingProjectResourceId, '/'))
output projectResourceId string = createProject ? project.id : existingProjectResourceId
output projectEndpoint string = createProject ? '${account.properties.endpoints['AI Foundry API']}api/projects/${projectName}' : ''
output accountPrincipalId string = account.identity.principalId
