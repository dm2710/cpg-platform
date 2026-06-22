import React, { useMemo, useState } from 'react'
import Topbar from '../components/layout/Topbar'
import BreakdownBarChart from '../components/charts/BreakdownBarChart'
import InsightPanel from '../components/common/InsightPanel'
import { Spinner, ErrorBanner, EmptyState } from '../components/common/States'
import { useFilters } from '../context/FilterContext'
import { useAsync } from '../hooks/useAsync'
import { getBreakdown, getDrivers, downloadCategoryCsv } from '../api/client'
import { formatCurrency, formatNumber, daysAgo, today } from '../utils/format'

export default function CategoryAnalysis() {
  const { regionId, lookbackDays } = useFilters()
  const [driverResult, setDriverResult] = useState(null)
  const [driverLoading, setDriverLoading] = useState(false)
  const [driverError, setDriverError] = useState(null)

  const params = useMemo(
    () => ({
      region_id: regionId || undefined,
      start_date: daysAgo(lookbackDays),
      end_date: today(),
      dimension: 'category',
    }),
    [regionId, lookbackDays]
  )

  const breakdown = useAsync(() => getBreakdown(params), [regionId, lookbackDays])
  const rows = (breakdown.data || []).map((d) => ({ label: d.label, revenue: Number(d.revenue), quantity: Number(d.quantity), pct: d.pct }))
  const totalRevenue = rows.reduce((s, r) => s + r.revenue, 0)

  const runDriverAnalysis = () => {
    setDriverLoading(true)
    setDriverError(null)
    getDrivers({ region_id: regionId || undefined, lookback_days: lookbackDays })
      .then(setDriverResult)
      .catch(setDriverError)
      .finally(() => setDriverLoading(false))
  }

  return (
    <>
      <Topbar
        title="Category analysis"
        description="Revenue contribution by product category, with AI-generated driver analysis."
      />

      <div className="card section">
        <div className="card__header">
          <div>
            <h2 className="card__title">Revenue by category</h2>
            <p className="card__subtitle">Last {lookbackDays} days · {formatCurrency(totalRevenue, { compact: true })} total</p>
          </div>
          <button className="btn btn--sm" onClick={() => downloadCategoryCsv({ start_date: daysAgo(lookbackDays), end_date: today() })}>
            <i className="ti ti-file-spreadsheet" aria-hidden="true" />
            CSV
          </button>
        </div>

        {breakdown.loading && <Spinner label="Loading categories…" />}
        {breakdown.error && <ErrorBanner message={breakdown.error.message} onRetry={breakdown.refetch} />}
        {!breakdown.loading && !breakdown.error && rows.length === 0 && (
          <EmptyState icon="category-2" title="No category data" description="No revenue recorded for this window." />
        )}
        {!breakdown.loading && !breakdown.error && rows.length > 0 && (
          <BreakdownBarChart data={rows} height={Math.max(220, rows.length * 44)} />
        )}
      </div>

      {!breakdown.loading && !breakdown.error && rows.length > 0 && (
        <div className="card section">
          <div className="card__header">
            <h2 className="card__title">Category detail</h2>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Category</th>
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
        title="Revenue driver analysis"
        icon="trending-up"
        loading={driverLoading}
        error={driverError}
        result={driverResult}
        onRegenerate={runDriverAnalysis}
      />
    </>
  )
}
