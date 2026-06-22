import React from 'react'
import './States.css'

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="state-block state-block--loading" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorBanner({ message, onRetry }) {
  return (
    <div className="state-block state-block--error" role="alert">
      <i className="ti ti-alert-triangle" aria-hidden="true" />
      <span>{message || 'Something went wrong loading this data.'}</span>
      {onRetry && (
        <button className="state-block__retry" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function EmptyState({ icon = 'inbox', title, description, action }) {
  return (
    <div className="empty-state">
      <i className={`ti ti-${icon}`} aria-hidden="true" />
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  )
}
