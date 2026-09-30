// =====================================================================================
// Cosmos DB (NoSQL) - Edge IQ operational + memory store
// Containers:
//   assets            : canonical edge device / asset registry (Fabric IQ ontology mirror)
//   telemetry_summary : rolled-up per-device health & KPI snapshots
//   workorders        : maintenance work orders and dispatch state
//   alarms            : SCADA / edge alarm events and acknowledgement state
//   compliance        : regulatory sample results and reporting state
//   agent_memory      : orchestrator conversation threads + agent scratchpad
//   agent_traces      : per-turn routing decisions for observability/replay
// =====================================================================================
@description('Cosmos DB account name.')
param accountName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Database name.')
param databaseName string = 'edgeiq'

@description('Use serverless capacity mode.')
param serverless bool = true

@description('Provisioned RU/s per container when serverless is false.')
param throughput int = 1000

@description('Principal IDs granted the Cosmos DB Built-in Data Contributor role.')
param principalIds array = []

@description('Default TTL (seconds) for telemetry_summary. -1 disables expiry.')
param telemetrySummaryTtlSeconds int = 7776000

@description('Default TTL (seconds) for agent_memory threads.')
param agentMemoryTtlSeconds int = 2592000

var containers = [
  { name: 'assets', partitionKey: '/siteId', ttl: -1 }
  { name: 'telemetry_summary', partitionKey: '/deviceId', ttl: telemetrySummaryTtlSeconds }
  { name: 'workorders', partitionKey: '/siteId', ttl: -1 }
  { name: 'alarms', partitionKey: '/deviceId', ttl: -1 }
  { name: 'compliance', partitionKey: '/siteId', ttl: -1 }
  { name: 'agent_memory', partitionKey: '/conversationId', ttl: agentMemoryTtlSeconds }
  { name: 'agent_traces', partitionKey: '/conversationId', ttl: agentMemoryTtlSeconds }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [
      { locationName: location, failoverPriority: 0, isZoneRedundant: false }
    ]
    capabilities: serverless ? [{ name: 'EnableServerless' }] : []
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: { id: databaseName }
  }
}

resource cosmosContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for c in containers: {
  parent: database
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: { paths: [c.partitionKey], kind: 'Hash' }
      defaultTtl: c.ttl
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [{ path: '/*' }]
        excludedPaths: [{ path: '/"_etag"/?' }]
      }
    }
    options: serverless ? {} : { throughput: throughput }
  }
}]

// Cosmos DB Built-in Data Contributor (data-plane, keyless access)
resource dataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [for (pid, i) in principalIds: {
  parent: account
  name: guid(account.id, pid, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: pid
    scope: account.id
  }
}]

output resourceId string = account.id
output name string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
output containerNames array = [for c in containers: c.name]
