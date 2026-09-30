import React from 'react';

/**
 * Shows which specialist took the question and why.
 *
 * This panel is the reason the console exists. A chat box that just returns
 * prose is indistinguishable from a single model with a good prompt. Exposing
 * the strategy, the agents and the matched entities is what makes the routing
 * auditable - an operator acting on a maintenance recommendation is entitled
 * to know which agent produced it.
 */
export default function RoutePanel({ route }) {
  if (!route) return null;

  const agents = route.agents?.length ? route.agents : ['magentic-plan'];
  const entities = route.entities ?? {};
  const confidence = typeof route.confidence === 'number' ? route.confidence : null;

  return (
    <div className="route-panel">
      <div className="route-header">
        <span className="route-strategy">{route.strategy ?? 'route'}</span>
        {confidence !== null && (
          <span
            className="route-confidence"
            title="Router confidence in this specialist selection"
          >
            {Math.round(confidence * 100)}% confidence
          </span>
        )}
      </div>

      <div className="route-agents">
        {agents.map((a) => (
          <span key={a} className="agent-chip">
            {a}
          </span>
        ))}
      </div>

      {route.rationale && <p className="route-rationale">{route.rationale}</p>}

      {Object.keys(entities).length > 0 && (
        <dl className="route-entities">
          {Object.entries(entities).map(([key, value]) => (
            <div key={key} className="entity-row">
              <dt>{key}</dt>
              <dd>{Array.isArray(value) ? value.join(', ') : String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
