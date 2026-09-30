// =====================================================================================
// Storage Account (ADLS Gen2) - landing zone for edge telemetry batches and the
// document corpus. Shortcut these containers into OneLake from the Fabric Lakehouse.
// =====================================================================================
@description('Storage account name (3-24 lowercase chars).')
param storageAccountName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Principal IDs granted Storage Blob Data Contributor.')
param principalIds array = []

@description('Blob containers created for the Edge IQ medallion layout.')
param containerNames array = [
  'bronze-edge-telemetry'
  'silver-edge-curated'
  'gold-edge-analytics'
  'knowledge-base'
  'device-configs'
]

var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    networkAcls: { bypass: 'AzureServices', defaultAction: 'Allow' }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in containerNames: {
  parent: blobService
  name: name
  properties: { publicAccess: 'None' }
}]

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(storage.id, pid, storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: pid
  }
}]

output resourceId string = storage.id
output name string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output dfsEndpoint string = storage.properties.primaryEndpoints.dfs
output containerNames array = containerNames
