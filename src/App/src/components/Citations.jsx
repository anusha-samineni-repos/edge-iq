import React from 'react';

/**
 * Citation list, keyed to the [n] markers the orchestrator rewrites into the
 * answer text (_MARKER_RE turns Foundry's U+3010n:src U+3011 form into [n]).
 *
 * The classification badge is shown deliberately. Retrieval is filtered at
 * query time by the governance ceiling, so anything listed here was already
 * permitted - but showing the label lets a reviewer confirm the ceiling was
 * applied as intended rather than taking it on trust.
 */
export default function Citations({ citations }) {
  if (!citations?.length) return null;

  return (
    <details className="citations" open>
      <summary>
        Evidence <span className="count">{citations.length}</span>
      </summary>
      <ol className="citation-list">
        {citations.map((c, i) => {
          const n = c.index ?? i + 1;
          const title = c.title ?? c.source ?? `Source ${n}`;
          const cls = (c.classification ?? '').toLowerCase();
          return (
            <li key={`${title}-${n}`} className="citation">
              <span className="citation-index">[{n}]</span>
              <div className="citation-body">
                <div className="citation-title">
                  {c.url ? (
                    <a href={c.url} target="_blank" rel="noreferrer">
                      {title}
                    </a>
                  ) : (
                    title
                  )}
                  {cls && <span className={`classification ${cls}`}>{cls}</span>}
                </div>
                {c.source && c.source !== title && (
                  <div className="citation-source">{c.source}</div>
                )}
                {c.snippet && <p className="citation-snippet">{c.snippet}</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </details>
  );
}
