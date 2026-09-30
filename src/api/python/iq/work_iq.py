"""
Work IQ - the Microsoft 365 productivity/context brain.

Gives Edge IQ the human half of the story: the SharePoint O&M library, the
Teams channel where the night shift discussed a pump trip, the Outlook thread
with the vendor's RMA number, and the operator's own calendar when scheduling
a maintenance window.

All access is delegated (On-Behalf-Of) so a user only ever sees M365 content
they already have rights to. With no Graph app registered the class degrades
into a clearly-labelled placeholder rather than failing the turn - this is the
plug-in point customers wire to their own tenant.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import WorkIQSettings

logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    """A single piece of M365 context (document, message, mail, event)."""

    id: str
    kind: str  # document | chat | mail | event | task
    title: str
    snippet: str
    web_url: str = ""
    author: str = ""
    timestamp: str = ""
    source: str = "work-iq"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkIQResult:
    items: list[WorkItem] = field(default_factory=list)
    configured: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "configured": self.configured,
            "note": self.note,
        }


_PLACEHOLDER_NOTE = (
    "Work IQ is not yet connected to a Microsoft 365 tenant. Register an Entra app with "
    "Sites.Read.All, ChannelMessage.Read.All, Mail.Read and Calendars.ReadWrite, then set "
    "M365_CLIENT_ID / M365_CLIENT_SECRET / M365_TENANT_ID. See docs/work-iq-setup.md."
)


class WorkIQ:
    """Delegated Microsoft Graph access for operational context."""

    def __init__(self, settings: WorkIQSettings, credential_factory=None):
        self.settings = settings
        self._credential_factory = credential_factory

    @property
    def configured(self) -> bool:
        return self.settings.enabled and self.settings.configured

    # ------------------------------------------------------------------ search
    async def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        top: int = 10,
        user_assertion: str | None = None,
    ) -> WorkIQResult:
        """Run a Microsoft Search query across the configured M365 surfaces."""
        kinds = kinds or ["document", "chat", "mail"]
        if not self.configured:
            return WorkIQResult(configured=False, note=_PLACEHOLDER_NOTE)

        entity_map = {
            "document": "driveItem",
            "chat": "chatMessage",
            "mail": "message",
            "event": "event",
            "task": "todoTask",
        }
        entity_types = [entity_map[k] for k in kinds if k in entity_map]

        try:
            import httpx

            token = await self._graph_token(user_assertion)
            body = {
                "requests": [
                    {
                        "entityTypes": entity_types,
                        "query": {"queryString": query},
                        "from": 0,
                        "size": top,
                    }
                ]
            }
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    f"{self.settings.graph_endpoint}/search/query",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                resp.raise_for_status()
                payload = resp.json()
            return WorkIQResult(items=_parse_search_response(payload), configured=True)
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Work IQ search failed: %s", exc)
            return WorkIQResult(configured=True, note=f"Work IQ search failed: {exc}")

    # ------------------------------------------------------- site-scoped docs
    async def site_documents(self, query: str, *, top: int = 5, user_assertion: str | None = None):
        """Search only the configured SharePoint O&M libraries."""
        if not self.settings.sharepoint_site_urls:
            return WorkIQResult(configured=self.configured, note="No SharePoint sites configured.")
        scoped = " OR ".join(f'site:"{url}"' for url in self.settings.sharepoint_site_urls)
        return await self.search(f"{query} ({scoped})", kinds=["document"], top=top, user_assertion=user_assertion)

    # ------------------------------------------------------ operations chatter
    async def operations_chatter(
        self, query: str, *, hours: int = 72, top: int = 10, user_assertion: str | None = None
    ) -> WorkIQResult:
        """Recent Teams discussion relevant to a device/site - the 'tribal knowledge' path."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d")
        return await self.search(
            f"{query} sent>={since}", kinds=["chat"], top=top, user_assertion=user_assertion
        )

    # --------------------------------------------------- maintenance scheduling
    async def propose_maintenance_window(
        self,
        attendee_upns: list[str],
        *,
        duration_minutes: int = 120,
        within_days: int = 7,
        user_assertion: str | None = None,
    ) -> dict:
        """
        Find a slot when the required crew is free (Graph findMeetingTimes).

        Returned verbatim to the Maintenance Planner agent so a work order can be
        scheduled against real human availability, not a guess.
        """
        if not self.configured:
            return {"configured": False, "note": _PLACEHOLDER_NOTE, "suggestions": []}
        try:
            import httpx

            token = await self._graph_token(user_assertion)
            now = datetime.now(timezone.utc)
            body = {
                "attendees": [
                    {"type": "required", "emailAddress": {"address": upn}} for upn in attendee_upns
                ],
                "timeConstraint": {
                    "activityDomain": "work",
                    "timeSlots": [
                        {
                            "start": {"dateTime": now.isoformat(), "timeZone": "UTC"},
                            "end": {
                                "dateTime": (now + timedelta(days=within_days)).isoformat(),
                                "timeZone": "UTC",
                            },
                        }
                    ],
                },
                "meetingDuration": f"PT{duration_minutes}M",
                "maxCandidates": 5,
            }
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    f"{self.settings.graph_endpoint}/me/findMeetingTimes",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                resp.raise_for_status()
                payload = resp.json()
            return {
                "configured": True,
                "suggestions": [
                    {
                        "start": s["meetingTimeSlot"]["start"]["dateTime"],
                        "end": s["meetingTimeSlot"]["end"]["dateTime"],
                        "confidence": s.get("confidence"),
                    }
                    for s in payload.get("meetingTimeSuggestions", [])
                ],
            }
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Work IQ findMeetingTimes failed: %s", exc)
            return {"configured": True, "note": str(exc), "suggestions": []}

    # ------------------------------------------------------------------ util
    async def _graph_token(self, user_assertion: str | None) -> str:
        if self._credential_factory is None:
            raise RuntimeError("No credential factory supplied to Work IQ")
        credential = await self._credential_factory(user_assertion)
        token = await credential.get_token("https://graph.microsoft.com/.default")
        return token.token


def _parse_search_response(payload: dict) -> list[WorkItem]:
    items: list[WorkItem] = []
    for response in payload.get("value", []):
        for container in response.get("hitsContainers", []):
            for hit in container.get("hits", []):
                resource = hit.get("resource", {}) or {}
                kind = _infer_kind(resource.get("@odata.type", ""))
                items.append(
                    WorkItem(
                        id=str(resource.get("id", hit.get("hitId", ""))),
                        kind=kind,
                        title=resource.get("name")
                        or resource.get("subject")
                        or (resource.get("body", {}) or {}).get("content", "")[:60]
                        or "Untitled",
                        snippet=hit.get("summary", "") or "",
                        web_url=resource.get("webUrl", "") or "",
                        author=(
                            (resource.get("createdBy", {}) or {}).get("user", {}) or {}
                        ).get("displayName", "")
                        or ((resource.get("from", {}) or {}).get("emailAddress", {}) or {}).get(
                            "name", ""
                        ),
                        timestamp=resource.get("lastModifiedDateTime")
                        or resource.get("createdDateTime", ""),
                    )
                )
    return items


def _infer_kind(odata_type: str) -> str:
    t = odata_type.lower()
    if "drive" in t:
        return "document"
    if "chatmessage" in t:
        return "chat"
    if "message" in t:
        return "mail"
    if "event" in t:
        return "event"
    return "document"
