import React, { useState } from 'react'
import Topbar from '../components/layout/Topbar'
import InsightPanel from '../components/common/InsightPanel'
import { useFilters } from '../context/FilterContext'
import {
  getTrendSummary, getRootCause, getForecastExplanation, getDrivers, getExecutiveSummary,
  downloadExecutivePdf,
} from '../api/client'

function useInsight(fetchFn) {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = (overrideArgs) => {
    setLoading(true)
    setError(null)
    fetchFn(overrideArgs)
      .then(setResult)
      .catch(setError)
      .finally(() => setLoading(false))
  }

  return { result, loading, error, run }
}

export default function AiInsights() {
  const { categoryId, regionId, lookbackDays, segmentLabel } = useFilters()
  const [changeDescription, setChangeDescription] = useState('Revenue dropped sharply in the last two weeks')

  const trend = useInsight(() =>
    getTrendSummary({ category_id: categoryId || undefined, region_id: regionId || undefined, lookback_days: lookbackDays })
  )
  const rootCause = useInsight(() =>
    getRootCause({
      change_description: changeDescription,
      category_id: categoryId || undefined,
      region_id: regionId || undefined,
      lookback_days: Math.min(lookbackDays, 180),
    })
  )
  const forecast = useInsight(() =>
    getForecastExplanation({ category_id: categoryId || undefined, region_id: regionId || undefined, horizon_days: 30 })
  )
  const drivers = useInsight(() =>
    getDrivers({ category_id: categoryId || undefined, region_id: regionId || undefined, lookback_days: lookbackDays })
  )
  const executive = useInsight(() =>
    getExecutiveSummary({
      category_id: categoryId || undefined,
      region_id: regionId || undefined,
      lookback_days: lookbackDays,
      horizon_days: 30,
    })
  )

  return (
    <>
      <Topbar
        title="AI insights"
        description="Five DeepSeek-powered analysis engines, grounded entirely in your live revenue data."
      />

      <div className="card section">
        <div className="card__header">
          <div>
            <h2 className="card__title">Executive summary</h2>
            <p className="card__subtitle">{segmentLabel} · board-ready report</p>
          </div>
          <button
            className="btn btn--accent btn--sm"
            onClick={() => downloadExecutivePdf({ category_id: categoryId || undefined, region_id: regionId || undefined, lookback_days: lookbackDays, horizon_days: 30 })}
          >
            <i className="ti ti-file-type-pdf" aria-hidden="true" />
            Download full PDF
          </button>
        </div>
      </div>

      <InsightPanel
        title="Executive summary"
        icon="presentation"
        loading={executive.loading}
        error={executive.error}
        result={executive.result}
        onRegenerate={executive.run}
      />

      <InsightPanel
        title="Trend summarization"
        icon="chart-line"
        loading={trend.loading}
        error={trend.error}
        result={trend.result}
        onRegenerate={trend.run}
      />

      <InsightPanel
        title="Revenue driver analysis"
        icon="trending-up"
        loading={drivers.loading}
        error={drivers.error}
        result={drivers.result}
        onRegenerate={drivers.run}
      />

      <InsightPanel
        title="Forecast explanation"
        icon="telescope"
        loading={forecast.loading}
        error={forecast.error}
        result={forecast.result}
        onRegenerate={forecast.run}
      />

      <div className="card insight-panel">
        <div className="card__header">
          <div className="insight-panel__heading">
            <i className="ti ti-search" aria-hidden="true" />
            <h2 className="card__title">Root cause analysis</h2>
          </div>
        </div>
        <label style={{ display: 'block', marginBottom: 12 }}>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
            Describe the change you want analyzed
          </span>
          <input
            type="text"
            value={changeDescription}
            onChange={(e) => setChangeDescription(e.target.value)}
            style={{
              width: '100%', padding: '9px 12px', borderRadius: 8,
              border: '1px solid var(--border-subtle)', fontSize: 13.5, fontFamily: 'var(--font-ui)',
            }}
          />
        </label>
        <button className="btn btn--primary btn--sm" onClick={rootCause.run} disabled={rootCause.loading}>
          <i className="ti ti-bolt" aria-hidden="true" />
          {rootCause.loading ? 'Analyzing…' : 'Run analysis'}
        </button>

        {rootCause.error && (
          <p style={{ color: 'var(--negative)', fontSize: 13, marginTop: 12 }}>{rootCause.error.message}</p>
        )}
        {rootCause.result && (
          <p className="insight-text" style={{ marginTop: 16 }}>{rootCause.result.insightText}</p>
        )}
      </div>
    </>
  )
}
