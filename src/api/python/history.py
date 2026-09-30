"""Conversation history and audit-trace routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .orchestrator import Orchestrator, get_orchestrator

router = APIRouter(prefix="/api/history", tags=["history"])


def _user_id(request: Request) -> str:
    return request.headers.get("x-ms-client-principal-name") or request.headers.get(
        "x-edgeiq-user", "anonymous"
    )


@router.get("/conversations")
async def list_conversations(
    request: Request, orchestrator: Orchestrator = Depends(get_orchestrator)
):
    if orchestrator.store is None:
        raise HTTPException(status_code=503, detail="Conversation store unavailable")
    return {"conversations": await orchestrator.store.list_conversations(_user_id(request))}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, orchestrator: Orchestrator = Depends(get_orchestrator)
):
    if orchestrator.store is None:
        raise HTTPException(status_code=503, detail="Conversation store unavailable")
    return {
        "conversationId": conversation_id,
        "turns": await orchestrator.store.get_history(conversation_id, limit=100),
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, orchestrator: Orchestrator = Depends(get_orchestrator)
):
    if orchestrator.store is None:
        raise HTTPException(status_code=503, detail="Conversation store unavailable")
    deleted = await orchestrator.store.delete_conversation(conversation_id)
    return {"conversationId": conversation_id, "deleted": deleted}


@router.get("/traces/{conversation_id}")
async def get_traces(
    conversation_id: str, orchestrator: Orchestrator = Depends(get_orchestrator)
):
    """
    Full decision audit for a conversation: routing scores, which IQ layers
    answered, resolved assets, citations and gated actions.
    """
    if orchestrator.store is None:
        raise HTTPException(status_code=503, detail="Conversation store unavailable")
    return {
        "conversationId": conversation_id,
        "traces": await orchestrator.store.get_traces(conversation_id, limit=100),
    }
