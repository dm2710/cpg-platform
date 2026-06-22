import React, { useMemo, useState } from 'react'
import Topbar from '../components/layout/Topbar'
import BreakdownBarChart from '../components/charts/BreakdownBarChart'
import InsightPanel from '../components/common/InsightPanel'
import { Spinner, ErrorBanner, EmptyState } from '../components/common/States'
import { useFilters } from '../context/FilterContext'
import { useAsync } from '../hooks/useAsync'
import { getBreakdown, getTrendSummary, downloadRegionCsv } from '../api/client'
import { formatCurrency, formatNumber, daysAgo, today } from '../utils/format'

export default function RegionalAnalysis() {
  const { categoryId, lookbackDays } = useFilters()
  const [trendResult, setTrendResult] = useState(null)
  const [trendLoading, setTrendLoading] = useState(false)
  const [trendError, setTrendError] = useState(null)

  const params = useMemo(
    () => ({
      category_id: categoryId || undefined,
      start_date: daysAgo(lookbackDays),
      end_date: today(),
      dimension: 'region',
    }),
    [categoryId, lookbackDays]
  )

  const breakdown = useAsync(() => getBreakdown(params), [categoryId, lookbackDays])
  const rows = (breakdown.data || []).map((d) => ({ label: d.label, revenue: Number(d.revenue), quantity: Number(d.quantity), pct: d.pct }))
  const totalRevenue = rows.reduce((s, r) => s + r.revenue, 0)
  const sorted = [...rows].sort((a, b) => a.revenue - b.revenue)
  const lowest = sorted[0]
  const highest = sorted[sorted.length - 1]

  const runTrendAnalysis = () => {
    setTrendLoading(true)
    setTrendError(null)
    getTrendSummary({ category_id: categoryId || undefined, lookback_days: lookbackDays })
      .then(setTrendResult)
      .catch(setTrendError)
      .finally(() => setTrendLoading(false))
  }

  return (
    <>
      <Topbar
        title="Regional analysis"
        description="Revenue contribution by geographic market, with AI-generated trend narrative."
      />

      {!breakdown.loading && !breakdown.error && rows.length > 1 && (
        <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
          <div className="card" style={{ padding: '18px 20px' }}>
            <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
              Strongest region
            </div>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 500 }}>{highest?.label}</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }} className="mono">
              {formatCurrency(highest?.revenue, { compact: true })} · {highest?.pct?.toFixed(1)}% of total
            </div>
          </div>
          <div className="card" style={{ padding: '18px 20px' }}>
            <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
              Needs attention
            </div>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 500 }}>{lowest?.label}</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }} className="mono">
              {formatCurrency(lowest?.revenue, { compact: true })} · {lowest?.pct?.toFixed(1)}% of total
            </div>
          </div>
        </div>
      )}

      <div className="card section">
        <div className="card__header">
          <div>
            <h2 className="card__title">Revenue by region</h2>
            <p className="card__subtitle">Last {lookbackDays} days · {formatCurrency(totalRevenue, { compact: true })} total</p>
          </div>
          <button className="btn btn--sm" onClick={() => downloadRegionCsv({ start_date: daysAgo(lookbackDays), end_date: today() })}>
            <i className="ti ti-file-spreadsheet" aria-hidden="true" />
            CSV
          </button>
        </div>

        {breakdown.loading && <Spinner label="Loading regions…" />}
        {breakdown.error && <ErrorBanner message={breakdown.error.message} onRetry={breakdown.refetch} />}
        {!breakdown.loading && !breakdown.error && rows.length === 0 && (
          <EmptyState icon="map-2" title="No region data" description="No revenue recorded for this window." />
        )}
        {!breakdown.loading && !breakdown.error && rows.length > 0 && (
          <BreakdownBarChart data={rows} height={Math.max(220, rows.length * 44)} />
        )}
      </div>

      {!breakdown.loading && !breakdown.error && rows.length > 0 && (
        <div className="card section">
          <div className="card__header">
            <h2 className="card__title">Region detail</h2>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Region</th>
                <th>Revenue</th>
                <th>Units sold</th>
                <th>% of total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label}>
                  <td>{r.label}</td>
                  <td className="mono-cell">{formatCurrency(r.revenue)}</td>
                  <td className="mono-cell">{formatNumber(r.quantity)}</td>
                  <td className="mono-cell">{r.pct?.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <InsightPanel
        title="Regional trend narrative"
        icon="map-2"
        loading={trendLoading}
        error={trendError}
        result={trendResult}
        onRegenerate={runTrendAnalysis}
      />
    </>
  )
}
