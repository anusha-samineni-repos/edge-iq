"""
Edge IQ configuration.

Every knob the solution needs is resolved here, in this precedence order:

    1. Azure App Configuration  (AZURE_APPCONFIG_ENDPOINT)  - change at runtime, no redeploy
    2. Environment variables    (.env / container app env)   - change at deploy time
    3. Defaults declared below                               - safe local demo values

This mirrors the Microsoft IQ Solution Orchestrator pattern where the orchestrator
reads a single settings object and every IQ layer is independently togglable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _csv(key: str) -> list[str]:
    raw = _env(key)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class FoundryIQSettings:
    """Foundry IQ - the knowledge / RAG / agent-runtime brain."""

    project_endpoint: str = field(default_factory=lambda: _env("AZURE_AI_AGENT_ENDPOINT"))
    account_name: str = field(default_factory=lambda: _env("AZURE_AI_FOUNDRY_ACCOUNT"))
    chat_deployment: str = field(default_factory=lambda: _env("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o"))
    embedding_deployment: str = field(
        default_factory=lambda: _env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    )
    search_endpoint: str = field(default_factory=lambda: _env("AZURE_SEARCH_ENDPOINT"))
    search_index: str = field(default_factory=lambda: _env("AZURE_SEARCH_INDEX", "edgeiq-knowledge"))
    search_semantic_config: str = field(
        default_factory=lambda: _env("AZURE_SEARCH_SEMANTIC_CONFIG", "edgeiq-semantic")
    )
    top_k: int = field(default_factory=lambda: _int("FOUNDRY_IQ_TOP_K", 8))
    reranker_threshold: float = field(
        default_factory=lambda: float(_env("FOUNDRY_IQ_RERANKER_THRESHOLD", "1.8"))
    )
    enabled: bool = field(default_factory=lambda: _bool("FOUNDRY_IQ_ENABLED", True))

    @property
    def configured(self) -> bool:
        return bool(self.project_endpoint)


@dataclass
class FabricIQSettings:
    """Fabric IQ - the business-data / semantic-ontology brain over OneLake."""

    workspace_name: str = field(default_factory=lambda: _env("FABRIC_WORKSPACE_NAME", "EdgeIQ-WaterUtility"))
    workspace_id: str = field(default_factory=lambda: _env("FABRIC_WORKSPACE_ID"))
    lakehouse_name: str = field(default_factory=lambda: _env("FABRIC_LAKEHOUSE_NAME", "EdgeIQ_Lakehouse"))
    lakehouse_id: str = field(default_factory=lambda: _env("FABRIC_LAKEHOUSE_ID"))
    eventhouse_name: str = field(default_factory=lambda: _env("FABRIC_EVENTHOUSE_NAME", "EdgeIQ_Telemetry"))
    kql_endpoint: str = field(default_factory=lambda: _env("FABRIC_KQL_ENDPOINT"))
    kql_database: str = field(default_factory=lambda: _env("FABRIC_KQL_DATABASE", "EdgeIQ_Telemetry"))
    onelake_endpoint: str = field(
        default_factory=lambda: _env("FABRIC_ONELAKE_ENDPOINT", "https://onelake.dfs.fabric.microsoft.com")
    )
    sql_endpoint: str = field(default_factory=lambda: _env("FABRIC_SQL_ENDPOINT"))
    data_agent_url: str = field(default_factory=lambda: _env("FABRIC_DATA_AGENT_URL"))
    ontology_path: str = field(
        default_factory=lambda: _env("FABRIC_ONTOLOGY_PATH", "fabric/ontology/water_utility_ontology.yaml")
    )
    enabled: bool = field(default_factory=lambda: _bool("FABRIC_IQ_ENABLED", True))

    @property
    def onelake_lakehouse_uri(self) -> str:
        """abfss URI of the Lakehouse Tables root in OneLake."""
        return (
            f"abfss://{self.workspace_name}@onelake.dfs.fabric.microsoft.com/"
            f"{self.lakehouse_name}.Lakehouse/Tables"
        )

    @property
    def configured(self) -> bool:
        return bool(self.kql_endpoint or self.sql_endpoint or self.data_agent_url)


@dataclass
class WorkIQSettings:
    """Work IQ - the Microsoft 365 productivity/context brain (Graph-backed)."""

    tenant_id: str = field(default_factory=lambda: _env("M365_TENANT_ID"))
    client_id: str = field(default_factory=lambda: _env("M365_CLIENT_ID"))
    graph_endpoint: str = field(
        default_factory=lambda: _env("M365_GRAPH_ENDPOINT", "https://graph.microsoft.com/v1.0")
    )
    sharepoint_site_urls: list[str] = field(default_factory=lambda: _csv("M365_SHAREPOINT_SITE_URLS"))
    teams_channel_ids: list[str] = field(default_factory=lambda: _csv("M365_TEAMS_CHANNEL_IDS"))
    outlook_shared_mailboxes: list[str] = field(
        default_factory=lambda: _csv("M365_OUTLOOK_SHARED_MAILBOXES")
    )
    # Graph connector external item collection holding indexed O&M documents
    graph_connector_id: str = field(default_factory=lambda: _env("M365_GRAPH_CONNECTOR_ID"))
    enabled: bool = field(default_factory=lambda: _bool("WORK_IQ_ENABLED", True))

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.tenant_id)


@dataclass
class CosmosSettings:
    """Cosmos DB - operational registry, alarms, work orders and agent memory."""

    endpoint: str = field(default_factory=lambda: _env("AZURE_COSMOS_ENDPOINT"))
    database: str = field(default_factory=lambda: _env("AZURE_COSMOS_DATABASE", "edgeiq"))
    assets_container: str = field(default_factory=lambda: _env("COSMOS_ASSETS_CONTAINER", "assets"))
    telemetry_container: str = field(
        default_factory=lambda: _env("COSMOS_TELEMETRY_CONTAINER", "telemetry_summary")
    )
    workorders_container: str = field(default_factory=lambda: _env("COSMOS_WORKORDERS_CONTAINER", "workorders"))
    alarms_container: str = field(default_factory=lambda: _env("COSMOS_ALARMS_CONTAINER", "alarms"))
    compliance_container: str = field(default_factory=lambda: _env("COSMOS_COMPLIANCE_CONTAINER", "compliance"))
    memory_container: str = field(default_factory=lambda: _env("COSMOS_MEMORY_CONTAINER", "agent_memory"))
    traces_container: str = field(default_factory=lambda: _env("COSMOS_TRACES_CONTAINER", "agent_traces"))

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)


@dataclass
class CopilotStudioSettings:
    """Copilot Studio front-door agent placeholders."""

    environment_id: str = field(default_factory=lambda: _env("COPILOT_STUDIO_ENVIRONMENT_ID"))
    agent_schema_name: str = field(
        default_factory=lambda: _env("COPILOT_STUDIO_AGENT_SCHEMA_NAME", "edgeiq_water_edge_agent")
    )
    direct_line_secret_ref: str = field(
        default_factory=lambda: _env("COPILOT_STUDIO_DIRECTLINE_SECRET_REF", "copilot-directline-secret")
    )
    # Shared secret Copilot Studio presents when calling the orchestrator
    inbound_api_key_ref: str = field(
        default_factory=lambda: _env("COPILOT_STUDIO_INBOUND_KEY_REF", "copilot-inbound-key")
    )

    @property
    def configured(self) -> bool:
        return bool(self.environment_id)


@dataclass
class EdgeSettings:
    """Edge / IoT plane settings."""

    iothub_name: str = field(default_factory=lambda: _env("AZURE_IOTHUB_NAME"))
    iothub_hostname: str = field(default_factory=lambda: _env("AZURE_IOTHUB_HOSTNAME"))
    eventhub_namespace: str = field(default_factory=lambda: _env("AZURE_EVENTHUB_NAMESPACE"))
    eventhub_telemetry: str = field(default_factory=lambda: _env("AZURE_EVENTHUB_TELEMETRY", "edge-telemetry"))
    eventhub_consumer_group: str = field(
        default_factory=lambda: _env("AZURE_EVENTHUB_CONSUMER_GROUP", "edgeiq-agents")
    )


@dataclass
class OrchestratorSettings:
    """Solution Orchestrator behaviour."""

    name: str = field(default_factory=lambda: _env("EDGEIQ_ORCHESTRATOR_NAME", "Edge IQ Solution Orchestrator"))
    # 'magentic' = Agent Framework Magentic manager plans + delegates (default)
    # 'handoff'  = router agent hands the turn to exactly one specialist
    # 'single'   = one agent with every tool attached (lowest latency, least control)
    mode: str = field(default_factory=lambda: _env("EDGEIQ_ORCHESTRATION_MODE", "magentic"))
    max_rounds: int = field(default_factory=lambda: _int("EDGEIQ_MAX_ROUNDS", 12))
    max_stall_rounds: int = field(default_factory=lambda: _int("EDGEIQ_MAX_STALL_ROUNDS", 3))
    request_timeout_seconds: int = field(default_factory=lambda: _int("EDGEIQ_REQUEST_TIMEOUT", 180))
    mcp_gateway_url: str = field(
        default_factory=lambda: _env("EDGEIQ_MCP_GATEWAY_URL", "http://localhost:8080")
    )
    enable_tracing: bool = field(default_factory=lambda: _bool("EDGEIQ_ENABLE_TRACING", True))
    # Highest Foundry IQ classification the orchestrator may surface by default.
    # public < internal < confidential < restricted
    max_classification: str = field(
        default_factory=lambda: _env("EDGEIQ_MAX_CLASSIFICATION", "internal")
    )
    # When true the orchestrator answers from the bundled demo dataset and never
    # calls Azure. Lets the whole solution be demoed with zero cloud resources.
    demo_mode: bool = field(default_factory=lambda: _bool("EDGEIQ_DEMO_MODE", False))
    demo_data_path: str = field(default_factory=lambda: _env("EDGEIQ_DEMO_DATA_PATH", "data/customdata"))


@dataclass
class Settings:
    """Root settings object injected everywhere."""

    foundry_iq: FoundryIQSettings = field(default_factory=FoundryIQSettings)
    fabric_iq: FabricIQSettings = field(default_factory=FabricIQSettings)
    work_iq: WorkIQSettings = field(default_factory=WorkIQSettings)
    cosmos: CosmosSettings = field(default_factory=CosmosSettings)
    copilot_studio: CopilotStudioSettings = field(default_factory=CopilotStudioSettings)
    edge: EdgeSettings = field(default_factory=EdgeSettings)
    orchestrator: OrchestratorSettings = field(default_factory=OrchestratorSettings)

    appconfig_endpoint: str = field(default_factory=lambda: _env("AZURE_APPCONFIG_ENDPOINT"))
    key_vault_endpoint: str = field(default_factory=lambda: _env("AZURE_KEY_VAULT_ENDPOINT"))
    managed_identity_client_id: str = field(default_factory=lambda: _env("AZURE_CLIENT_ID"))
    app_insights_connection_string: str = field(
        default_factory=lambda: _env("APPLICATIONINSIGHTS_CONNECTION_STRING")
    )

    def describe(self) -> dict:
        """Human-readable readiness report; surfaced by GET /api/health."""
        return {
            "orchestrator": {
                "name": self.orchestrator.name,
                "mode": self.orchestrator.mode,
                "demo_mode": self.orchestrator.demo_mode,
                "mcp_gateway": self.orchestrator.mcp_gateway_url,
            },
            "iq_layers": {
                "foundry_iq": {
                    "enabled": self.foundry_iq.enabled,
                    "configured": self.foundry_iq.configured,
                    "index": self.foundry_iq.search_index,
                },
                "fabric_iq": {
                    "enabled": self.fabric_iq.enabled,
                    "configured": self.fabric_iq.configured,
                    "lakehouse": self.fabric_iq.lakehouse_name,
                    "onelake": self.fabric_iq.onelake_lakehouse_uri,
                },
                "work_iq": {
                    "enabled": self.work_iq.enabled,
                    "configured": self.work_iq.configured,
                    "sharepoint_sites": len(self.work_iq.sharepoint_site_urls),
                },
            },
            "stores": {
                "cosmos": self.cosmos.configured,
                "iot_hub": bool(self.edge.iothub_hostname),
            },
            "channels": {"copilot_studio": self.copilot_studio.configured},
        }


def _hydrate_from_app_configuration() -> None:
    """Pull EdgeIQ:* keys from App Configuration into os.environ before settings build."""
    endpoint = _env("AZURE_APPCONFIG_ENDPOINT")
    if not endpoint:
        return
    try:
        from azure.appconfiguration import AzureAppConfigurationClient

        from .auth import get_credential

        client = AzureAppConfigurationClient(base_url=endpoint, credential=get_credential())
        mapping = {
            "EdgeIQ:FabricIQ:WorkspaceName": "FABRIC_WORKSPACE_NAME",
            "EdgeIQ:FabricIQ:LakehouseName": "FABRIC_LAKEHOUSE_NAME",
            "EdgeIQ:FabricIQ:EventhouseName": "FABRIC_EVENTHOUSE_NAME",
            "EdgeIQ:FabricIQ:KqlEndpoint": "FABRIC_KQL_ENDPOINT",
            "EdgeIQ:FabricIQ:DataAgentUrl": "FABRIC_DATA_AGENT_URL",
            "EdgeIQ:WorkIQ:TenantId": "M365_TENANT_ID",
            "EdgeIQ:WorkIQ:ClientId": "M365_CLIENT_ID",
            "EdgeIQ:WorkIQ:SharePointSiteUrls": "M365_SHAREPOINT_SITE_URLS",
            "EdgeIQ:WorkIQ:TeamsChannelIds": "M365_TEAMS_CHANNEL_IDS",
            "EdgeIQ:CopilotStudio:EnvironmentId": "COPILOT_STUDIO_ENVIRONMENT_ID",
            "EdgeIQ:CopilotStudio:AgentSchemaName": "COPILOT_STUDIO_AGENT_SCHEMA_NAME",
            "EdgeIQ:Orchestrator:Mode": "EDGEIQ_ORCHESTRATION_MODE",
        }
        for key, env_name in mapping.items():
            try:
                setting = client.get_configuration_setting(key=key)
            except Exception:  # key simply absent
                continue
            if setting and setting.value:
                os.environ[env_name] = setting.value
        logger.info("Hydrated configuration from App Configuration at %s", endpoint)
    except Exception as exc:  # pragma: no cover - best-effort hydration
        logger.warning("App Configuration hydration skipped: %s", exc)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _hydrate_from_app_configuration()
    return Settings()
