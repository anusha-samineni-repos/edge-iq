// =====================================================================================
// Azure AI Search - vector + semantic index backing Foundry IQ knowledge retrieval
// over SOPs, O&M manuals, regulatory texts and incident runbooks.
// =====================================================================================
@description('Search service name.')
param searchServiceName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Search SKU.')
@allowed(['basic', 'standard', 'standard2'])
param sku string = 'basic'

@description('Replica count.')
param replicaCount int = 1

@description('Partition count.')
param partitionCount int = 1

@description('Principal IDs granted Search Index Data Contributor + Search Service Contributor.')
param principalIds array = []

var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: { name: sku }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    authOptions: null
    disableLocalAuth: true
    semanticSearch: sku == 'basic' ? 'free' : 'standard'
  }
}

resource indexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(search.id, pid, searchIndexDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    principalId: pid
  }
}]

resource serviceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(search.id, pid, searchServiceContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
    principalId: pid
  }
}]

output resourceId string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
