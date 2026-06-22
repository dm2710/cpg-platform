import React from 'react'
import { trendDirection } from '../../utils/format'
import './KpiCard.css'

export default function KpiCard({ label, value, sublabel, trendPct, accent = false, icon }) {
  const dir = trendDirection(trendPct)

  return (
    <div className={`kpi-card${accent ? ' kpi-card--accent' : ''}`}>
      <div className="kpi-card__label">
        {icon && <i className={`ti ti-${icon}`} aria-hidden="true" />}
        {label}
      </div>
      <div className="kpi-card__value">{value}</div>
      <div className="kpi-card__footer">
        {trendPct !== undefined && trendPct !== null && (
          <span className={`kpi-card__trend kpi-card__trend--${dir}`}>
            <i className={`ti ti-trending-${dir === 'down' ? 'down' : 'up'}`} aria-hidden="true" />
            {Math.abs(trendPct).toFixed(1)}%
          </span>
        )}
        {sublabel && <span className="kpi-card__sublabel">{sublabel}</span>}
      </div>
    </div>
  )
}
