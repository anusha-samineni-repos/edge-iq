// =====================================================================================
// Microsoft Fabric capacity - hosts the Edge IQ workspace containing:
//   - Lakehouse (EdgeIQ_Lakehouse) on OneLake  : bronze/silver/gold edge telemetry
//   - Eventhouse / KQL DB (EdgeIQ_Telemetry)   : real-time edge signal store
//   - Eventstream                              : Event Hub -> Eventhouse + Lakehouse
//   - Semantic model + ontology                : Fabric IQ business entities
//   - Fabric Data Agent                        : NL2Ontology query surface for agents
// Workspace-level artifacts are created post-provision (see infra/scripts/seed).
// =====================================================================================
@description('Fabric capacity name (lowercase, 3-24 chars).')
param capacityName string

@description('Azure region. Must be a Fabric-supported region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Fabric capacity SKU.')
@allowed(['F2', 'F4', 'F8', 'F16', 'F32', 'F64', 'F128'])
param sku string = 'F2'

@description('Entra object IDs or UPNs that administer the capacity. Defaults to the deploying tenant admin.')
param adminMembers array = []

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  tags: tags
  sku: { name: sku, tier: 'Fabric' }
  properties: {
    administration: {
      members: empty(adminMembers) ? [tenant().tenantId] : adminMembers
    }
  }
}

output resourceId string = capacity.id
output name string = capacity.name
output sku string = sku
