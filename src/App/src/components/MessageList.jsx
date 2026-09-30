import React, { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import RoutePanel from './RoutePanel.jsx';
import LayerStatus from './LayerStatus.jsx';
import Citations from './Citations.jsx';
import ApprovalPanel from './ApprovalPanel.jsx';

function Timings({ elapsedMs }) {
  if (typeof elapsedMs !== 'number') return null;
  return <span className="elapsed">{(elapsedMs / 1000).toFixed(1)}s</span>;
}

export default function MessageList({ messages, status, onApprove }) {
  const endRef = useRef(null);

  // Follow the stream, but only while the user is already near the bottom -
  // yanking the viewport away from someone reading earlier evidence is worse
  // than letting new tokens arrive off-screen.
  useEffect(() => {
    const el = endRef.current;
    if (!el) return;
    const container = el.parentElement;
    if (!container) return;
    const distance =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distance < 260) {
      el.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages, status]);

  return (
    <div className="messages">
      {messages.length === 0 && (
        <div className="empty-state">
          <h2>Ask Edge IQ anything about the estate</h2>
          <p>
            Edge IQ assembles context from Work IQ, Fabric IQ and Foundry IQ,
            then routes to the specialist best placed to answer - asset health,
            water quality, leak detection, energy, fleet operations, knowledge
            or maintenance planning.
          </p>
        </div>
      )}

      {messages.map((m) => (
        <article key={m.id} className={`message ${m.role}`}>
          <div className="message-role">
            {m.role === 'user' ? 'You' : 'Edge IQ'}
            <Timings elapsedMs={m.elapsedMs} />
          </div>

          {m.role === 'assistant' && m.route && <RoutePanel route={m.route} />}

          <div className="message-body">
            {m.role === 'assistant' ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {m.content || (m.streaming ? '' : '_No content returned._')}
              </ReactMarkdown>
            ) : (
              m.content
            )}
            {m.streaming && <span className="cursor" aria-hidden="true" />}
          </div>

          {m.role === 'assistant' && (
            <>
              <ApprovalPanel approvals={m.pendingApprovals} onApprove={onApprove} />
              <Citations citations={m.citations} />
              {m.layerStatus && <LayerStatus layerStatus={m.layerStatus} compact />}
            </>
          )}

          {m.error && <div className="message-error">{m.error}</div>}
        </article>
      ))}

      {status && (
        <div className="status-line" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span className="status-stage">{status.stage}</span>
          <span className="status-detail">{status.detail}</span>
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}
