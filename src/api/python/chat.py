"""Chat routes: streaming and non-streaming orchestrator turns."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agents.routing import describe_routing_table
from .orchestrator import Orchestrator, get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversationId: str | None = None
    stream: bool = True
    maxClassification: str | None = Field(
        default=None,
        description="public | internal | confidential | restricted",
    )


def _user_id(request: Request) -> str:
    principal = request.headers.get("x-ms-client-principal-name")
    return principal or request.headers.get("x-edgeiq-user") or "anonymous"


def _user_assertion(authorization: str | None) -> str | None:
    """Extract the bearer token for On-Behalf-Of access to Microsoft 365."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return None


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    user_id = _user_id(request)
    assertion = _user_assertion(authorization)

    if not payload.stream:
        result = await orchestrator.ask(
            payload.message,
            conversation_id=payload.conversationId,
            user_id=user_id,
            user_assertion=assertion,
            max_classification=payload.maxClassification,
        )
        return result.to_dict()

    async def event_stream():
        try:
            async for event in orchestrator.ask_stream(
                payload.message,
                conversation_id=payload.conversationId,
                user_id=user_id,
                user_assertion=assertion,
                max_classification=payload.maxClassification,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # pragma: no cover
            logger.exception("Stream failed: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/agents")
async def list_agents():
    """The live agent roster - the UI and docs both read from this."""
    return {"agents": describe_routing_table()}


@router.post("/route-preview")
async def route_preview(payload: ChatRequest):
    """Show which specialist would answer, without running a model. Great for demos."""
    from .agents.routing import route

    return route(payload.message).to_dict()


@router.get("/context-preview")
async def context_preview(
    q: str,
    authorization: str | None = Header(default=None),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    """Return the raw unified context for a question - the IQ layer inspector."""
    from .iq import build_unified_context

    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    context = await build_unified_context(
        q,
        foundry_iq=orchestrator.foundry_iq,
        fabric_iq=orchestrator.fabric_iq,
        work_iq=orchestrator.work_iq,
        user_assertion=_user_assertion(authorization),
    )
    return context.to_dict()
