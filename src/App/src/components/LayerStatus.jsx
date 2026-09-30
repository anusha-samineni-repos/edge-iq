import React from 'react';

/**
 * Per-layer status for Work IQ / Fabric IQ / Foundry IQ.
 *
 * Every layer degrades independently - build_unified_context() gathers them
 * with return_exceptions=True, so a missing Graph consent or an unprovisioned
 * Fabric workspace downgrades one layer instead of failing the turn. Making
 * that visible matters: an answer grounded in two of three layers is still
 * useful, but the operator should know which evidence was unavailable.
 */

const LAYER_LABELS = {
  work_iq: 'Work IQ',
  workIq: 'Work IQ',
  fabric_iq: 'Fabric IQ',
  fabricIq: 'Fabric IQ',
  foundry_iq: 'Foundry IQ',
  foundryIq: 'Foundry IQ',
};

const LAYER_HINTS = {
  'Work IQ': 'Microsoft 365 signals - Teams, SharePoint, Outlook, Planner',
  'Fabric IQ': 'Operational data over OneLake, resolved through the water ontology',
  'Foundry IQ': 'Governed knowledge - SOPs, standards, regulatory guidance',
};

function toneOf(status) {
  const s = String(status).toLowerCase();
  if (s.includes('ok') || s.includes('ready') || s.includes('live')) return 'ok';
  if (s.includes('demo') || s.includes('local') || s.includes('placeholder')) return 'demo';
  if (s.includes('error') || s.includes('fail')) return 'error';
  return 'muted';
}

export default function LayerStatus({ layerStatus, compact = false }) {
  if (!layerStatus || Object.keys(layerStatus).length === 0) return null;

  return (
    <div className={compact ? 'layer-status compact' : 'layer-status'}>
      {Object.entries(layerStatus).map(([key, status]) => {
        const label = LAYER_LABELS[key] ?? key;
        return (
          <div
            key={key}
            className={`layer-pill ${toneOf(status)}`}
            title={LAYER_HINTS[label] ? `${LAYER_HINTS[label]} - ${status}` : String(status)}
          >
            <span className="layer-name">{label}</span>
            <span className="layer-value">{String(status)}</span>
          </div>
        );
      })}
    </div>
  );
}
