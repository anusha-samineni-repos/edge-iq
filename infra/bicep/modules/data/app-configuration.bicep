// =====================================================================================
// App Configuration - single place to change Edge IQ runtime parameters without
// redeploying. Holds Fabric IQ, Work IQ / M365 and Copilot Studio placeholders.
// =====================================================================================
@description('App Configuration store name.')
param configStoreName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Principal IDs granted App Configuration Data Reader.')
param principalIds array = []

@description('Key/value pairs seeded into the store.')
param keyValues array = []

var appConfigDataReader = '516239f1-63e1-4d78-a4de-a74fb236a071'

resource configStore 'Microsoft.AppConfiguration/configurationStores@2024-05-01' = {
  name: configStoreName
  location: location
  tags: tags
  sku: { name: 'standard' }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource kvPairs 'Microsoft.AppConfiguration/configurationStores/keyValues@2024-05-01' = [for kv in keyValues: {
  parent: configStore
  name: kv.key
  properties: {
    value: kv.value
    contentType: 'text/plain'
  }
}]

resource acRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(configStore.id, pid, appConfigDataReader)
  scope: configStore
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', appConfigDataReader)
    principalId: pid
  }
}]

output resourceId string = configStore.id
output name string = configStore.name
output endpoint string = configStore.properties.endpoint
