import React from 'react';
import LayerStatus from './LayerStatus.jsx';

/**
 * Starter prompts, each wired to a fault that actually exists in the seeded
 * dataset. A demo that asks about a healthy asset proves nothing, so every
 * one of these has a real signal behind it - see data/scenarios.
 */
const SAMPLES = [
  {
    uc: 'UC1',
    label: 'Pump vibration trend',
    prompt:
      'Vibration on WTP-01-PUMP-003 has been climbing. What is the ISO 10816-3 zone, and what should we do?',
  },
  {
    uc: 'UC2',
    label: 'Chlorine residual breach',
    prompt:
      'Did any chlorine residual breach compliance limits in the last 30 days, and which sites were affected?',
  },
  {
    uc: 'UC2b',
    label: 'Suspect analyser',
    prompt:
      'Is the chlorine analyser at RES-07 reporting correctly, or do we have an instrument fault?',
  },
  {
    uc: 'UC3',
    label: 'Night flow / leakage',
    prompt:
      'Minimum night flow in DMA-14 looks wrong. Is there a leak, and how much water are we losing?',
  },
  {
    uc: 'UC4',
    label: 'Pump energy',
    prompt:
      'Which pumps lost efficiency this month, and what is the cost exposure at peak tariff?',
  },
  {
    uc: 'UC5',
    label: 'Edge fleet health',
    prompt:
      'Show me edge gateways that are offline, drifting from their twin, or have certificates expiring soon.',
  },
];

export default function Sidebar({
  health,
  conversations,
  activeId,
  onSelect,
  onNew,
  onSample,
  onDelete,
}) {
  const mode = health?.mode ?? (health ? 'unknown' : 'connecting');
  const ready = health?.status === 'ready';

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">IQ</div>
        <div>
          <div className="brand-name">Edge IQ</div>
          <div className="brand-sub">Water Utility Operations</div>
        </div>
      </div>

      <button type="button" className="new-chat" onClick={onNew}>
        New conversation
      </button>

      <section className="sidebar-section">
        <h3>Try an operational question</h3>
        <div className="samples">
          {SAMPLES.map((s) => (
            <button
              key={s.uc}
              type="button"
              className="sample"
              onClick={() => onSample(s.prompt)}
              title={s.prompt}
            >
              <span className="sample-uc">{s.uc}</span>
              <span className="sample-label">{s.label}</span>
            </button>
          ))}
        </div>
      </section>

      {conversations?.length > 0 && (
        <section className="sidebar-section conversations">
          <h3>Recent</h3>
          <ul>
            {conversations.map((c) => {
              const id = c.conversationId ?? c.id;
              return (
                <li key={id} className={id === activeId ? 'active' : ''}>
                  <button type="button" onClick={() => onSelect(id)}>
                    {c.title || c.preview || id.slice(0, 8)}
                  </button>
                  <button
                    type="button"
                    className="delete"
                    onClick={() => onDelete(id)}
                    aria-label="Delete conversation"
                  >
                    &times;
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="sidebar-section health">
        <h3>System</h3>
        <div className={`health-badge ${ready ? 'ok' : 'warn'}`}>
          {ready ? 'Ready' : mode === 'connecting' ? 'Connecting' : 'Degraded'}
          {health?.mode && <span className="mode">{health.mode}</span>}
        </div>
        {health?.layerStatus && <LayerStatus layerStatus={health.layerStatus} />}
        {health?.demoMode && (
          <p className="demo-note">
            Demo mode - answers are grounded in the generated dataset, no Azure
            resources required.
          </p>
        )}
      </section>
    </aside>
  );
}
