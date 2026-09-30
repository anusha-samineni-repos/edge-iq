// =====================================================================================
// Container Apps environment hosting:
//   edgeiq-api          : Solution Orchestrator API (FastAPI + Agent Framework)
//   edgeiq-web          : React operator console
//   edgeiq-mcp-gateway  : multiplexed MCP server host (5 water utility MCP servers)
// =====================================================================================
@description('Container Apps environment name.')
param environmentAppName string

@description('Container registry name.')
param containerRegistryName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Image tag for all apps.')
param imageTag string = 'latest'

@description('Minimum replicas.')
param minReplicas int = 0

@description('Maximum replicas.')
param maxReplicas int = 3

@description('User-assigned managed identity resource ID.')
param managedIdentityResourceId string

@description('User-assigned managed identity client ID.')
param managedIdentityClientId string

@description('User-assigned managed identity principal ID.')
param managedIdentityPrincipalId string

@description('Log Analytics workspace customer ID.')
param logAnalyticsCustomerId string

@description('Log Analytics shared key.')
@secure()
param logAnalyticsSharedKey string

@description('Application Insights connection string.')
@secure()
param appInsightsConnectionString string

@description('Environment variables shared by all apps.')
param settings object = {}

@description('CPU cores per container.')
param cpu string = '1.0'

@description('Memory per container.')
param memory string = '2.0Gi'

var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
// azd replaces these images at `azd deploy` time; the placeholder keeps the first
// provision green. Set IMAGE_TAG to pin a pre-built tag from the ACR instead.
var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var resolvedImages = {
  api: imageTag == 'latest' ? placeholderImage : '${registry.properties.loginServer}/edgeiq-api:${imageTag}'
  web: imageTag == 'latest' ? placeholderImage : '${registry.properties.loginServer}/edgeiq-web:${imageTag}'
  mcp: imageTag == 'latest' ? placeholderImage : '${registry.properties.loginServer}/edgeiq-mcp:${imageTag}'
}
var sharedEnv = [for k in items(settings): { name: k.key, value: k.value }]

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, managedIdentityPrincipalId, acrPullRole)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
    principalId: managedIdentityPrincipalId
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentAppName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

// ---- MCP gateway: hosts the 5 water utility MCP servers over Streamable HTTP ----
resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'edgeiq-mcp-gateway'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${managedIdentityResourceId}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8080, transport: 'auto' }
      registries: [{ server: registry.properties.loginServer, identity: managedIdentityResourceId }]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: resolvedImages.mcp
          resources: { cpu: json(cpu), memory: memory }
          env: concat(sharedEnv, [
            { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'MCP_TRANSPORT', value: 'streamable-http' }
            { name: 'PORT', value: '8080' }
          ])
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
  dependsOn: [acrPull]
}

// ---- Solution Orchestrator API ----
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'edgeiq-api'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${managedIdentityResourceId}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [{ server: registry.properties.loginServer, identity: managedIdentityResourceId }]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: resolvedImages.api
          resources: { cpu: json(cpu), memory: memory }
          env: concat(sharedEnv, [
            { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'EDGEIQ_MCP_GATEWAY_URL', value: 'https://${mcpApp.properties.configuration.ingress.fqdn}' }
            { name: 'PORT', value: '8000' }
          ])
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
  dependsOn: [acrPull]
}

// ---- Operator console ----
resource webApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'edgeiq-web'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${managedIdentityResourceId}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 80, transport: 'auto' }
      registries: [{ server: registry.properties.loginServer, identity: managedIdentityResourceId }]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: resolvedImages.web
          resources: { cpu: json('0.5'), memory: '1.0Gi' }
          env: [
            { name: 'API_BASE_URL', value: 'https://${apiApp.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
  dependsOn: [acrPull]
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output environmentName string = env.name
output environmentId string = env.id
output apiAppName string = apiApp.name
output apiAppUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output webAppName string = webApp.name
output webAppUrl string = 'https://${webApp.properties.configuration.ingress.fqdn}'
output mcpAppName string = mcpApp.name
output mcpGatewayUrl string = 'https://${mcpApp.properties.configuration.ingress.fqdn}'
