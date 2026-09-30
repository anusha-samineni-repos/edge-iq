"""
Conversation memory and audit traces (Cosmos DB NoSQL).

Two containers:

  agent_memory  /conversationId, TTL 30 days - turn-by-turn conversation history
                so the orchestrator has continuity across questions.
  agent_traces  /conversationId - one document per turn recording the routing
                decision, which IQ layers answered, which agents ran, tool calls
                and citations. This is the audit record a water utility needs to
                explain why an agent recommended an action.

Both degrade to an in-process store when Cosmos is not configured, which keeps
demo mode and unit tests free of external dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, settings: Settings, credential: Any = None):
        self.settings = settings
        self._credential = credential
        self._client = None
        self._memory = None
        self._traces = None
        self._local: dict[str, list[dict]] = {}
        self._local_traces: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()
        self.backend = "memory"

    async def connect(self) -> "ConversationStore":
        cfg = self.settings.cosmos
        if not cfg.endpoint or self.settings.orchestrator.demo_mode:
            logger.info("ConversationStore using in-process backend (no Cosmos endpoint).")
            return self
        try:
            from azure.cosmos.aio import CosmosClient

            self._client = CosmosClient(cfg.endpoint, credential=self._credential)
            db = self._client.get_database_client(cfg.database)
            self._memory = db.get_container_client(cfg.memory_container)
            self._traces = db.get_container_client(cfg.traces_container)
            self.backend = "cosmos"
            logger.info("ConversationStore connected to Cosmos DB %s/%s", cfg.endpoint, cfg.database)
        except Exception as exc:
            logger.warning("Cosmos unavailable (%s); falling back to in-process memory.", exc)
        return self

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------ memory
    async def append_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        user_id: str = "anonymous",
        metadata: dict | None = None,
    ) -> dict:
        doc = {
            "id": str(uuid.uuid4()),
            "conversationId": conversation_id,
            "userId": user_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": _now(),
            "type": "turn",
        }
        if self._memory is not None:
            try:
                await self._memory.upsert_item(doc)
                return doc
            except Exception as exc:
                logger.warning("Cosmos memory write failed: %s", exc)
        async with self._lock:
            self._local.setdefault(conversation_id, []).append(doc)
        return doc

    async def get_history(self, conversation_id: str, limit: int = 20) -> list[dict]:
        if self._memory is not None:
            try:
                query = (
                    "SELECT TOP @limit c.role, c.content, c.createdAt, c.metadata "
                    "FROM c WHERE c.conversationId = @cid AND c.type = 'turn' "
                    "ORDER BY c.createdAt DESC"
                )
                items = self._memory.query_items(
                    query=query,
                    parameters=[
                        {"name": "@cid", "value": conversation_id},
                        {"name": "@limit", "value": limit},
                    ],
                )
                rows = [item async for item in items]
                return list(reversed(rows))
            except Exception as exc:
                logger.warning("Cosmos memory read failed: %s", exc)
        async with self._lock:
            return list(self._local.get(conversation_id, []))[-limit:]

    async def list_conversations(self, user_id: str, limit: int = 25) -> list[dict]:
        if self._memory is not None:
            try:
                query = (
                    "SELECT TOP @limit c.conversationId, c.content, c.createdAt FROM c "
                    "WHERE c.userId = @uid AND c.role = 'user' ORDER BY c.createdAt DESC"
                )
                items = self._memory.query_items(
                    query=query,
                    parameters=[
                        {"name": "@uid", "value": user_id},
                        {"name": "@limit", "value": limit},
                    ],
                )
                seen: dict[str, dict] = {}
                async for item in items:
                    cid = item["conversationId"]
                    if cid not in seen:
                        seen[cid] = {
                            "conversationId": cid,
                            "title": (item.get("content") or "")[:80],
                            "createdAt": item.get("createdAt"),
                        }
                return list(seen.values())
            except Exception as exc:
                logger.warning("Cosmos conversation list failed: %s", exc)
        async with self._lock:
            out = []
            for cid, turns in self._local.items():
                first_user = next((t for t in turns if t["role"] == "user"), None)
                if first_user and first_user.get("userId") == user_id:
                    out.append(
                        {
                            "conversationId": cid,
                            "title": first_user["content"][:80],
                            "createdAt": first_user["createdAt"],
                        }
                    )
            return out[:limit]

    async def delete_conversation(self, conversation_id: str) -> int:
        if self._memory is not None:
            try:
                items = self._memory.query_items(
                    query="SELECT c.id FROM c WHERE c.conversationId = @cid",
                    parameters=[{"name": "@cid", "value": conversation_id}],
                )
                count = 0
                async for item in items:
                    await self._memory.delete_item(item["id"], partition_key=conversation_id)
                    count += 1
                return count
            except Exception as exc:
                logger.warning("Cosmos delete failed: %s", exc)
        async with self._lock:
            return len(self._local.pop(conversation_id, []))

    # ------------------------------------------------------------------ traces
    async def write_trace(self, conversation_id: str, trace: dict) -> dict:
        doc = {
            "id": str(uuid.uuid4()),
            "conversationId": conversation_id,
            "createdAt": _now(),
            "type": "trace",
            **trace,
        }
        if self._traces is not None:
            try:
                await self._traces.upsert_item(doc)
                return doc
            except Exception as exc:
                logger.warning("Cosmos trace write failed: %s", exc)
        async with self._lock:
            self._local_traces.setdefault(conversation_id, []).append(doc)
        return doc

    async def get_traces(self, conversation_id: str, limit: int = 20) -> list[dict]:
        if self._traces is not None:
            try:
                items = self._traces.query_items(
                    query=(
                        "SELECT TOP @limit * FROM c WHERE c.conversationId = @cid "
                        "ORDER BY c.createdAt DESC"
                    ),
                    parameters=[
                        {"name": "@cid", "value": conversation_id},
                        {"name": "@limit", "value": limit},
                    ],
                )
                return [item async for item in items]
            except Exception as exc:
                logger.warning("Cosmos trace read failed: %s", exc)
        async with self._lock:
            return list(self._local_traces.get(conversation_id, []))[-limit:]

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "database": self.settings.cosmos.database,
            "memoryContainer": self.settings.cosmos.memory_container,
            "tracesContainer": self.settings.cosmos.traces_container,
        }
