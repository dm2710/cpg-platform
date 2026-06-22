import React from 'react'
import './ConfidenceBadge.css'

export default function ConfidenceBadge({ confidence }) {
  if (confidence === undefined || confidence === null) return null
  const pct = Math.round(confidence * 100)
  let tier = 'low'
  if (confidence >= 0.85) tier = 'high'
  else if (confidence >= 0.65) tier = 'medium'

  return (
    <span className={`confidence-badge confidence-badge--${tier}`}>
      <i className="ti ti-shield-check" aria-hidden="true" />
      {pct}% confidence
    </span>
  )
}
