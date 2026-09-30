"""Credential helpers. Prefers user-assigned managed identity, falls back to azd/CLI.

``azure-identity`` is treated as optional so demo mode, unit tests and CI can run
without the Azure SDK installed. When it is missing every helper returns ``None``
and the downstream clients fall back to their local corpora.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
    from azure.identity.aio import ManagedIdentityCredential as AsyncManagedIdentityCredential
    from azure.identity.aio import OnBehalfOfCredential

    IDENTITY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    DefaultAzureCredential = ManagedIdentityCredential = None  # type: ignore[assignment]
    AsyncDefaultAzureCredential = AsyncManagedIdentityCredential = None  # type: ignore[assignment]
    OnBehalfOfCredential = None  # type: ignore[assignment]
    IDENTITY_AVAILABLE = False
    logger.info("azure-identity not installed; Edge IQ will run against local corpora only.")


@lru_cache(maxsize=1)
def get_credential():
    if not IDENTITY_AVAILABLE:
        return None
    client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    if client_id:
        logger.info("Using user-assigned managed identity %s", client_id)
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


async def get_credential_async(user_assertion: str | None = None):
    """
    Async credential.

    When *user_assertion* (an Entra access token for the signed-in operator) is
    supplied, an On-Behalf-Of credential is returned so Work IQ / Fabric IQ calls
    honour that user's Microsoft 365 and OneLake permissions rather than the
    app identity. This is what keeps Edge IQ inside the tenant's trust boundary.
    """
    if not IDENTITY_AVAILABLE:
        return None

    tenant_id = os.getenv("M365_TENANT_ID") or os.getenv("AZURE_TENANT_ID", "")
    client_id = os.getenv("M365_CLIENT_ID", "").strip()
    client_secret = os.getenv("M365_CLIENT_SECRET", "").strip()

    if user_assertion and tenant_id and client_id and client_secret:
        logger.debug("Using on-behalf-of credential for delegated Work IQ access")
        return OnBehalfOfCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            user_assertion=user_assertion,
        )

    mi_client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    if mi_client_id:
        return AsyncManagedIdentityCredential(client_id=mi_client_id)
    return AsyncDefaultAzureCredential(exclude_interactive_browser_credential=True)
