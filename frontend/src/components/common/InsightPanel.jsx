import React from 'react'
import ConfidenceBadge from '../common/ConfidenceBadge'
import { Spinner, ErrorBanner } from '../common/States'
import './InsightPanel.css'

export default function InsightPanel({ title, icon, loading, error, result, onRegenerate }) {
  return (
    <div className="card insight-panel">
      <div className="card__header">
        <div className="insight-panel__heading">
          <i className={`ti ti-${icon}`} aria-hidden="true" />
          <h2 className="card__title">{title}</h2>
        </div>
        <div className="insight-panel__actions">
          {result && <ConfidenceBadge confidence={result.confidence} />}
          {result?.fromCache && <span className="insight-panel__cache-tag">cached</span>}
          <button className="btn btn--ghost btn--sm" onClick={onRegenerate} disabled={loading}>
            <i className="ti ti-refresh" aria-hidden="true" />
            Regenerate
          </button>
        </div>
      </div>

      {loading && <Spinner label="Generating insight…" />}
      {error && <ErrorBanner message={error.message} onRetry={onRegenerate} />}
      {!loading && !error && result && (
        <p className="insight-text">{result.insightText}</p>
      )}
      {!loading && !error && !result && (
        <p className="insight-text" style={{ color: 'var(--text-tertiary)' }}>
          No insight generated yet.
        </p>
      )}
    </div>
  )
}
