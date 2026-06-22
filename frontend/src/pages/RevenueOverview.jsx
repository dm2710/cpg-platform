import React, { useMemo } from 'react'
import Topbar from '../components/layout/Topbar'
import KpiCard from '../components/common/KpiCard'
import RevenueTrendChart from '../components/charts/RevenueTrendChart'
import BreakdownBarChart from '../components/charts/BreakdownBarChart'
import { Spinner, ErrorBanner, EmptyState } from '../components/common/States'
import { useFilters } from '../context/FilterContext'
import { useAsync } from '../hooks/useAsync'
import { getRevenue, getBreakdown, getSummary, downloadRevenueCsv, downloadExecutivePdf } from '../api/client'
import { formatCurrency, formatNumber, daysAgo, today } from '../utils/format'

export default function RevenueOverview() {
  const { categoryId, regionId, lookbackDays, segmentLabel } = useFilters()

  const params = useMemo(
    () => ({
      category_id: categoryId || undefined,
      region_id: regionId || undefined,
      start_date: daysAgo(lookbackDays),
      end_date: today(),
    }),
    [categoryId, regionId, lookbackDays]
  )

  const revenue = useAsync(() => getRevenue({ ...params, granularity: 'day' }), [
    categoryId, regionId, lookbackDays,
  ])
  const catBreakdown = useAsync(() => getBreakdown({ ...params, dimension: 'category' }), [
    categoryId, regionId, lookbackDays,
  ])
  const regBreakdown = useAsync(() => getBreakdown({ ...params, dimension: 'region' }), [
    categoryId, regionId, lookbackDays,
  ])
  const summary = useAsync(() => getSummary(), [])

  const series = revenue.data || []
  const totalRevenue = series.reduce((s, r) => s + Number(r.revenue || 0), 0)
  const totalQty = series.reduce((s, r) => s + Number(r.quantity || 0), 0)
  const avgDaily = series.length ? totalRevenue / series.length : 0

  const half = Math.floor(series.length / 2)
  const firstHalf = series.slice(0, half).reduce((s, r) => s + Number(r.revenue || 0), 0)
  const secondHalf = series.slice(half).reduce((s, r) => s + Number(r.revenue || 0), 0)
  const trendPct = firstHalf > 0 ? ((secondHalf - firstHalf) / firstHalf) * 100 : null

  const catData = (catBreakdown.data || []).map((d) => ({ label: d.label, revenue: Number(d.revenue) }))
  const regData = (regBreakdown.data || []).map((d) => ({ label: d.label, revenue: Number(d.revenue) }))

  const loading = revenue.loading || catBreakdown.loading || regBreakdown.loading
  const error = revenue.error || catBreakdown.error || regBreakdown.error

  return (
    <>
      <Topbar
        title="Revenue overview"
        description="Daily revenue, trend, and top contributors for the selected segment and window."
      />

      <div className="kpi-grid">
        <KpiCard label="Total revenue" value={formatCurrency(totalRevenue, { compact: true })} trendPct={trendPct} icon="currency-dollar" accent />
        <KpiCard label="Units sold" value={formatNumber(totalQty)} icon="package" />
        <KpiCard label="Avg daily revenue" value={formatCurrency(avgDaily, { compact: true })} icon="calendar-stats" />
        <KpiCard
          label="Platform total (all time)"
          value={summary.data ? formatCurrency(Number(summary.data.totalRevenue), { compact: true }) : '—'}
          sublabel={summary.data ? `${summary.data.categoryCount} categories · ${summary.data.regionCount} regions` : ''}
          icon="database"
        />
      </div>

      <div className="card section">
        <div className="card__header">
          <div>
            <h2 className="card__title">Revenue trend</h2>
            <p className="card__subtitle">{segmentLabel} · last {lookbackDays} days</p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn--sm" onClick={() => downloadRevenueCsv(params)}>
              <i className="ti ti-file-spreadsheet" aria-hidden="true" />
              CSV
            </button>
            <button
              className="btn btn--accent btn--sm"
              onClick={() => downloadExecutivePdf({ category_id: categoryId || undefined, region_id: regionId || undefined, lookback_days: lookbackDays })}
            >
              <i className="ti ti-file-type-pdf" aria-hidden="true" />
              Executive PDF
            </button>
          </div>
        </div>

        {revenue.loading && <Spinner label="Loading revenue series…" />}
        {revenue.error && <ErrorBanner message={revenue.error.message} onRetry={revenue.refetch} />}
        {!revenue.loading && !revenue.error && series.length === 0 && (
          <EmptyState icon="chart-area-line" title="No revenue data" description="No transactions found for this segment and window." />
        )}
        {!revenue.loading && !revenue.error && series.length > 0 && <RevenueTrendChart data={series} />}
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card__header">
            <h2 className="card__title">Top categories</h2>
          </div>
          {catBreakdown.loading && <Spinner label="Loading…" />}
          {catBreakdown.error && <ErrorBanner message={catBreakdown.error.message} onRetry={catBreakdown.refetch} />}
          {!catBreakdown.loading && !catBreakdown.error && catData.length === 0 && (
            <EmptyState icon="category-2" title="No category data" />
          )}
          {!catBreakdown.loading && !catBreakdown.error && catData.length > 0 && (
            <BreakdownBarChart data={catData} height={Math.max(180, catData.length * 42)} />
          )}
        </div>

        <div className="card">
          <div className="card__header">
            <h2 className="card__title">Top regions</h2>
          </div>
          {regBreakdown.loading && <Spinner label="Loading…" />}
          {regBreakdown.error && <ErrorBanner message={regBreakdown.error.message} onRetry={regBreakdown.refetch} />}
          {!regBreakdown.loading && !regBreakdown.error && regData.length === 0 && (
            <EmptyState icon="map-2" title="No region data" />
          )}
          {!regBreakdown.loading && !regBreakdown.error && regData.length > 0 && (
            <BreakdownBarChart data={regData} height={Math.max(180, regData.length * 42)} />
          )}
        </div>
      </div>
    </>
  )
}
