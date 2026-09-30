// =====================================================================================
// IoT Hub - device management plane for water utility edge gateways (Azure IoT Edge).
// Telemetry is routed to Event Hubs, which feeds the Fabric Eventstream -> Eventhouse.
// =====================================================================================
@description('IoT Hub name.')
param iotHubName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object = {}

@description('IoT Hub SKU.')
@allowed(['F1', 'S1', 'S2', 'S3'])
param sku string = 'S1'

@description('IoT Hub capacity units.')
param capacity int = 1

@description('Target Event Hub name for telemetry routing.')
param telemetryEventHubName string

@description('Authorization rule resource ID with Send rights on the target Event Hub.')
param telemetryAuthRuleId string

@description('Principal IDs granted IoT Hub Data Contributor.')
param principalIds array = []

var iotHubDataContributor = '4fc6c259-987e-4a07-842e-c321cc9d413f'

resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' = {
  name: iotHubName
  location: location
  tags: tags
  sku: { name: sku, capacity: capacity }
  properties: {
    minTlsVersion: '1.2'
    routing: {
      endpoints: {
        eventHubs: [
          {
            name: 'edgeiq-telemetry'
            connectionString: '${listKeys(telemetryAuthRuleId, '2024-01-01').primaryConnectionString};EntityPath=${telemetryEventHubName}'
            authenticationType: 'keyBased'
          }
        ]
      }
      routes: [
        {
          name: 'telemetry-to-eventhub'
          source: 'DeviceMessages'
          condition: 'true'
          endpointNames: ['edgeiq-telemetry']
          isEnabled: true
        }
        {
          name: 'twin-changes-to-eventhub'
          source: 'TwinChangeEvents'
          condition: 'true'
          endpointNames: ['edgeiq-telemetry']
          isEnabled: true
        }
        {
          name: 'device-lifecycle-to-builtin'
          source: 'DeviceLifecycleEvents'
          condition: 'true'
          endpointNames: ['events']
          isEnabled: true
        }
      ]
      fallbackRoute: {
        name: '$fallback'
        source: 'DeviceMessages'
        condition: 'true'
        endpointNames: ['events']
        isEnabled: true
      }
    }
  }
}

resource iotRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in principalIds: {
  name: guid(iotHub.id, pid, iotHubDataContributor)
  scope: iotHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', iotHubDataContributor)
    principalId: pid
  }
}]

output resourceId string = iotHub.id
output name string = iotHub.name
output hostName string = iotHub.properties.hostName
output builtInEventHubEndpoint string = iotHub.properties.eventHubEndpoints.events.endpoint
