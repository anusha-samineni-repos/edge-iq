@description('Key Vault name (3-24 chars, globally unique).')
param keyVaultName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Principal IDs granted Key Vault Secrets Officer.')
param principalIds array = []

@description('Enable purge protection. Required for production.')
param enablePurgeProtection bool = false

var keyVaultSecretsOfficer = '00482a5a-887f-4fb3-b363-3b7fe8e74483'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
    networkAcls: { bypass: 'AzureServices', defaultAction: 'Allow' }
  }
}

resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(keyVault.id, pid, keyVaultSecretsOfficer)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficer)
    principalId: pid
  }
}]

output resourceId string = keyVault.id
output name string = keyVault.name
output endpoint string = keyVault.properties.vaultUri
