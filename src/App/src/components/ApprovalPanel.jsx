import React from 'react';

/**
 * Human-in-the-loop gate for actions that change the physical world.
 *
 * Write tools are gated by two independent keys: the tool call must carry
 * approved=True AND the deployment must set EDGEIQ_ALLOW_WRITES=true. This
 * panel is the UI half of the first key. It never auto-approves, and the
 * second key stays an operator/deployment decision - a UI toggle must not be
 * able to arm writes on its own.
 */
export default function ApprovalPanel({ approvals, onApprove }) {
  if (!approvals?.length) return null;

  return (
    <div className="approval-panel" role="alert">
      <div className="approval-header">
        Approval required
        <span className="approval-sub">
          Edge IQ prepared these actions but did not commit them.
        </span>
      </div>

      <ul className="approval-list">
        {approvals.map((a, i) => {
          const key = a.id ?? a.tool ?? `approval-${i}`;
          return (
            <li key={key} className="approval-item">
              <div className="approval-action">{a.action ?? a.tool ?? 'Pending action'}</div>
              {a.summary && <p className="approval-summary">{a.summary}</p>}
              {a.target && <div className="approval-target">Target: {a.target}</div>}
              {a.reason && <div className="approval-reason">{a.reason}</div>}
              {onApprove && (
                <button
                  type="button"
                  className="approve-btn"
                  onClick={() => onApprove(a)}
                  title="Re-runs the request with approval granted for this action"
                >
                  Approve and continue
                </button>
              )}
            </li>
          );
        })}
      </ul>

      <p className="approval-note">
        Committing also requires <code>EDGEIQ_ALLOW_WRITES=true</code> on the
        deployment. In demo mode actions are always simulated.
      </p>
    </div>
  );
}
