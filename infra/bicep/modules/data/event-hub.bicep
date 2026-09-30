// =====================================================================================
// Event Hubs - hot path for edge telemetry streaming into Fabric Eventstream/Eventhouse
// =====================================================================================
@description('Event Hubs namespace name.')
param namespaceName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Throughput units.')
param capacity int = 1

@description('Message retention in days.')
param retentionDays int = 3

@description('Partition count for the telemetry hub.')
param partitionCount int = 4

@description('Principal IDs granted Azure Event Hubs Data Owner.')
param principalIds array = []

var eventHubsDataOwner = 'f526a384-b230-433a-b45c-95f59c4a2dec'
var telemetryHubName = 'edge-telemetry'
var alarmsHubName = 'edge-alarms'

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: namespaceName
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: capacity }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: false
    publicNetworkAccess: 'Enabled'
  }
}

resource telemetryHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: telemetryHubName
  properties: {
    messageRetentionInDays: retentionDays
    partitionCount: partitionCount
  }
}

resource alarmsHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: alarmsHubName
  properties: {
    messageRetentionInDays: retentionDays
    partitionCount: 2
  }
}

// Consumer group read by the Fabric Eventstream
resource fabricConsumerGroup 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: telemetryHub
  name: 'fabric-eventstream'
}

// Consumer group read by the Edge IQ anomaly detection agent tool
resource agentConsumerGroup 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: telemetryHub
  name: 'edgeiq-agents'
}

// Send-only rule used by IoT Hub routing
resource sendRule 'Microsoft.EventHub/namespaces/eventhubs/authorizationRules@2024-01-01' = {
  parent: telemetryHub
  name: 'iothub-send'
  properties: { rights: ['Send'] }
}

resource ehRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(namespace.id, pid, eventHubsDataOwner)
  scope: namespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubsDataOwner)
    principalId: pid
  }
}]

output resourceId string = namespace.id
output namespaceName string = namespace.name
output namespaceFqdn string = '${namespace.name}.servicebus.windows.net'
output telemetryHubName string = telemetryHub.name
output alarmsHubName string = alarmsHub.name
output sendAuthorizationRuleId string = sendRule.id
output fabricConsumerGroup string = fabricConsumerGroup.name
