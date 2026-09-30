import React, { useCallback, useEffect, useRef, useState } from 'react';
import Sidebar from './components/Sidebar.jsx';
import MessageList from './components/MessageList.jsx';
import {
  deleteConversation,
  fetchConversation,
  fetchConversations,
  fetchHealth,
  streamChat,
} from './api.js';

let seq = 0;
const nextId = () => `m${++seq}`;

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);

  // Governance ceiling for retrieval. Enforced server-side as a query-time
  // filter - this control selects it, it does not implement it.
  const [maxClassification, setMaxClassification] = useState('internal');

  const abortRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
    fetchConversations().then(setConversations).catch(() => {});
  }, []);

  const refreshConversations = useCallback(() => {
    fetchConversations().then(setConversations).catch(() => {});
  }, []);

  const send = useCallback(
    async (text) => {
      const question = (text ?? '').trim();
      if (!question || busy) return;

      setBusy(true);
      setInput('');
      setStatus({ stage: 'starting', detail: 'Contacting Edge IQ' });

      const userMsg = { id: nextId(), role: 'user', content: question };
      const replyId = nextId();
      const reply = {
        id: replyId,
        role: 'assistant',
        content: '',
        streaming: true,
      };
      setMessages((prev) => [...prev, userMsg, reply]);

      // Patch just the in-flight reply; the rest of the transcript is frozen.
      const patch = (fields) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === replyId ? { ...m, ...fields } : m)),
        );

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChat(
          {
            message: question,
            conversationId,
            maxClassification,
            signal: controller.signal,
          },
          (event) => {
            switch (event.type) {
              case 'status':
                setStatus({ stage: event.stage, detail: event.detail });
                break;

              // Note the nesting: the payload is {type:'route', route:{...}}.
              case 'route':
                patch({ route: event.route });
                break;

              case 'delta':
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === replyId ? { ...m, content: m.content + event.text } : m,
                  ),
                );
                break;

              case 'final': {
                const r = event.result ?? {};
                patch({
                  streaming: false,
                  content: r.answer || '',
                  citations: r.citations,
                  route: r.route,
                  layerStatus: r.layerStatus ?? r.layer_status,
                  pendingApprovals: r.pendingApprovals ?? r.pending_approvals,
                  elapsedMs: r.elapsedMs ?? r.elapsed_ms,
                });
                if (r.conversationId ?? r.conversation_id) {
                  setConversationId(r.conversationId ?? r.conversation_id);
                }
                break;
              }

              case 'error':
                patch({ streaming: false, error: event.message });
                break;

              default:
                break;
            }
          },
        );
      } catch (err) {
        if (err.name !== 'AbortError') {
          patch({ streaming: false, error: err.message });
        } else {
          patch({ streaming: false });
        }
      } finally {
        setBusy(false);
        setStatus(null);
        abortRef.current = null;
        refreshConversations();
        inputRef.current?.focus();
      }
    },
    [busy, conversationId, maxClassification, refreshConversations],
  );

  /**
   * Re-ask with approval granted. The server still requires
   * EDGEIQ_ALLOW_WRITES before anything is committed, so this grants one key,
   * never both.
   */
  const approve = useCallback(
    (approval) => {
      const action = approval.action ?? approval.tool ?? 'the proposed action';
      send(`Approved. Proceed with ${action}.`);
    },
    [send],
  );

  const stop = () => abortRef.current?.abort();

  const newConversation = () => {
    stop();
    setMessages([]);
    setConversationId(null);
    inputRef.current?.focus();
  };

  const openConversation = async (id) => {
    try {
      const data = await fetchConversation(id);
      const turns = data.messages ?? data.turns ?? [];
      setMessages(
        turns.map((t) => ({
          id: nextId(),
          role: t.role,
          content: t.content ?? t.text ?? '',
          elapsedMs: t.metadata?.elapsedMs,
        })),
      );
      setConversationId(id);
    } catch {
      /* a failed reload shouldn't blank the current transcript */
    }
  };

  const removeConversation = async (id) => {
    await deleteConversation(id);
    if (id === conversationId) newConversation();
    refreshConversations();
  };

  const onKeyDown = (e) => {
    // Enter sends, Shift+Enter breaks the line.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <div className="app">
      <Sidebar
        health={health}
        conversations={conversations}
        activeId={conversationId}
        onSelect={openConversation}
        onNew={newConversation}
        onSample={(p) => {
          setInput(p);
          inputRef.current?.focus();
        }}
        onDelete={removeConversation}
      />

      <main className="main">
        <MessageList messages={messages} status={status} onApprove={approve} />

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a device, site, DMA, compliance window or work order..."
            rows={1}
            disabled={busy}
          />

          <div className="composer-controls">
            <label className="classification-select">
              <span>Access</span>
              <select
                value={maxClassification}
                onChange={(e) => setMaxClassification(e.target.value)}
                title="Retrieval ceiling - documents above this classification are filtered out at query time"
              >
                <option value="public">Public</option>
                <option value="internal">Internal</option>
                <option value="confidential">Confidential</option>
              </select>
            </label>

            {busy ? (
              <button type="button" className="stop" onClick={stop}>
                Stop
              </button>
            ) : (
              <button type="submit" className="send" disabled={!input.trim()}>
                Ask
              </button>
            )}
          </div>
        </form>
      </main>
    </div>
  );
}
