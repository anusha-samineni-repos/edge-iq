/**
 * Edge IQ API client.
 *
 * The chat endpoint streams Server-Sent Events. We deliberately do not use the
 * browser EventSource API: it only does GET, and the turn needs a POST body
 * (message, conversation, classification ceiling). fetch + a ReadableStream
 * reader gives us POST and lets us surface errors properly.
 */

const DONE = '[DONE]';

/** GET /api/health - degraded layers are reported, not hidden. */
export async function fetchHealth() {
  // 503 is a legitimate answer here (still warming up), so don't throw on it.
  const res = await fetch('/api/health');
  return res.json();
}

/** GET /api/agents - the live roster, same source the docs quote. */
export async function fetchAgents() {
  const res = await fetch('/api/agents');
  if (!res.ok) throw new Error(`Agent roster unavailable (${res.status})`);
  const data = await res.json();
  return data.agents ?? [];
}

/** POST /api/route-preview - which specialist would take this, no model spend. */
export async function previewRoute(message) {
  const res = await fetch('/api/route-preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(`Route preview failed (${res.status})`);
  return res.json();
}

export async function fetchConversations() {
  const res = await fetch('/api/conversations');
  if (!res.ok) return [];
  const data = await res.json();
  return data.conversations ?? [];
}

export async function fetchConversation(conversationId) {
  const res = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`);
  if (!res.ok) throw new Error(`Conversation unavailable (${res.status})`);
  return res.json();
}

export async function deleteConversation(conversationId) {
  await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'DELETE',
  });
}

/**
 * POST /api/chat with stream=true, dispatching each event to `onEvent`.
 *
 * Event shapes (mirrors Orchestrator.ask_stream):
 *   {type:'status', stage, detail}
 *   {type:'route',  route:{...}}     <- note the nesting
 *   {type:'delta',  text}
 *   {type:'final',  result:{...}}
 *   {type:'error',  message}
 *
 * @returns {Promise<void>} resolves when the stream terminates.
 */
export async function streamChat(
  { message, conversationId, maxClassification, signal },
  onEvent,
) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      conversationId: conversationId ?? null,
      stream: true,
      maxClassification: maxClassification ?? null,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`Edge IQ returned ${res.status}. Check /api/health.`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. Keep the trailing partial
    // frame in the buffer - a chunk boundary can land mid-JSON.
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';

    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith('data:')) continue;

      const payload = line.slice(5).trim();
      if (payload === DONE) return;

      try {
        onEvent(JSON.parse(payload));
      } catch {
        // A malformed frame shouldn't kill an otherwise good turn.
        console.warn('Skipped unparseable SSE frame', payload);
      }
    }
  }
}
