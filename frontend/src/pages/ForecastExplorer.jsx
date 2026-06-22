import React, { useMemo, useState, useEffect } from 'react'
import Topbar from '../components/layout/Topbar'
import ForecastChart from '../components/charts/ForecastChart'
import InsightPanel from '../components/common/InsightPanel'
import { Spinner, ErrorBanner, EmptyState } from '../components/common/States'
import { useFilters } from '../context/FilterContext'
import { useAsync } from '../hooks/useAsync'
import {
  getForecasts, triggerTraining, triggerPrediction, getModels,
  getForecastExplanation, downloadForecastCsv,
} from '../api/client'
import { formatCurrency, formatPercent } from '../utils/format'

const HORIZON_OPTIONS = [7, 14, 30, 60, 90]

export default function ForecastExplorer() {
  const { categoryId, regionId, segmentLabel } = useFilters()
  const [horizonDays, setHorizonDays] = useState(30)
  const [training, setTraining] = useState(false)
  const [predicting, setPredicting] = useState(false)
  const [actionMessage, setActionMessage] = useState(null)
  const [actionError, setActionError] = useState(null)
  // Track whether training has completed at least once in this session,
  // so we can enable the forecast button even before models.refetch resolves.
  const [modelsTrained, setModelsTrained] = useState(false)

  const [explainResult, setExplainResult] = useState(null)
  const [explainLoading, setExplainLoading] = useState(false)
  const [explainError, setExplainError] = useState(null)

  const forecastParams = useMemo(
    () => ({ category_id: categoryId || undefined, region_id: regionId || undefined }),
    [categoryId, regionId]
  )

  // When the segment filter changes, reset training state so the predict button
  // correctly requires re-training (or finding an existing deployed model).
  useEffect(() => {
    setModelsTrained(false)
    setActionMessage(null)
    setActionError(null)
  }, [categoryId, regionId])

  const forecasts = useAsync(() => getForecasts(forecastParams), [categoryId, regionId])
  const models = useAsync(
    () => getModels({ segment_key: undefined, status: 'deployed' }),
    [categoryId, regionId]
  )

  const chartData = (forecasts.data || []).map((f) => ({
    date: f.forecastDate,
    predicted: Number(f.predictedRevenue),
    lower80: f.lower80 !== null ? Number(f.lower80) : null,
    upper80: f.upper80 !== null ? Number(f.upper80) : null,
    actual: f.actualRevenue !== null ? Number(f.actualRevenue) : null,
  }))

  const deployedModel = (models.data || []).find((m) =>
    (!categoryId || m.categoryId === categoryId) && (!regionId || m.regionId === regionId)
  ) || (models.data || [])[0] // fall back to any deployed model if no exact match

  // Allow predicting if training just succeeded in this session, or if the
  // model registry already has at least one deployed model.
  const canPredict = modelsTrained || (models.data || []).length > 0

  const handleTrain = async () => {
    setTraining(true)
    setActionError(null)
    setActionMessage(null)
    try {
      const result = await triggerTraining(
        {
          model_names: ['lightgbm'],
          horizon_days: horizonDays,
          category_ids: categoryId ? [categoryId] : undefined,
          region_ids: regionId ? [regionId] : undefined,
        },
        true
      )

      const trained = result.segmentsTrained ?? 0
      const failed  = result.segmentsFailed  ?? 0
      const skipped = result.segmentsSkipped ?? 0

      if (trained === 0) {
        // Training ran but no model was deployed — surface this as an error
        // so the user doesn't think they can proceed to forecast.
        const reason = skipped > 0 && failed === 0
          ? `All ${skipped} segment(s) were skipped — not enough historical data (need ≥60 days per segment). Try selecting a broader date range or removing filters.`
          : failed > 0
          ? `Training failed for ${failed} segment(s) and skipped ${skipped}. No model was deployed. Check server logs for details.`
          : 'No segments were trained — there may be insufficient data in the database.'
        setActionError(new Error(reason))
      } else {
        setActionMessage(
          `Training complete — ${trained} segment(s) trained` +
          (failed > 0 ? `, ${failed} failed` : '') +
          (result.avgMape ? `, avg MAPE ${result.avgMape.toFixed(1)}%` : '') +
          '.'
        )
        setModelsTrained(true)
      }
      // Await the refetch so the deployed-model info panel updates before
      // the user can click "Generate forecast".
      await models.refetch()
    } catch (err) {
      setActionError(err)
    } finally {
      setTraining(false)
    }
  }

  const handlePredict = async () => {
    setPredicting(true)
    setActionError(null)
    setActionMessage(null)
    try {
      // Use the batch endpoint so every trained segment gets a forecast,
      // regardless of whether the filter selects the exact trained segment.
      const result = await triggerPrediction(
        {
          category_ids:  categoryId   ? [categoryId]  : undefined,
          region_ids:    regionId     ? [regionId]    : undefined,
          horizon_days:  horizonDays,
        },
        true
      )
      const succeeded = result.segmentsForecast ?? 0
      const noModel   = result.segmentsNoModel  ?? 0
      if (succeeded === 0 && noModel > 0) {
        setActionError(new Error('No deployed model found for this segment. Please train a model first.'))
      } else {
        setActionMessage(
          `Forecast generated for the next ${horizonDays} days` +
          (succeeded > 0 ? ` across ${succeeded} segment(s)` : '') +
          '.'
        )
        await forecasts.refetch()
      }
    } catch (err) {
      setActionError(err)
    } finally {
      setPredicting(false)
    }
  }

  const runExplain = () => {
    setExplainLoading(true)
    setExplainError(null)
    getForecastExplanation({ category_id: categoryId || undefined, region_id: regionId || undefined, horizon_days: horizonDays })
      .then(setExplainResult)
      .catch(setExplainError)
      .finally(() => setExplainLoading(false))
  }

  return (
    <>
      <Topbar
        title="Forecast explorer"
        description="Train demand forecasting models and explore predictions with confidence bands."
      />

      <div className="card section">
        <div className="card__header">
          <div>
            <h2 className="card__title">Model controls</h2>
            <p className="card__subtitle">{segmentLabel}</p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginBottom: actionMessage || actionError ? 16 : 0 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, color: 'var(--text-secondary)' }}>
            Horizon
            <select
              value={horizonDays}
              onChange={(e) => setHorizonDays(Number(e.target.value))}
              style={{
                padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border-subtle)',
                fontSize: 13.5, fontFamily: 'var(--font-ui)', background: 'var(--bg-surface)',
              }}
            >
              {HORIZON_OPTIONS.map((h) => (
                <option key={h} value={h}>{h} days</option>
              ))}
            </select>
          </label>

          <button className="btn" onClick={handleTrain} disabled={training}>
            <i className="ti ti-cpu" aria-hidden="true" />
            {training ? 'Training…' : 'Train models'}
          </button>

          <button
            className="btn btn--accent"
            onClick={handlePredict}
            disabled={predicting || !canPredict}
            title={!canPredict ? 'Train a model first before generating a forecast' : undefined}
          >
            <i className="ti ti-telescope" aria-hidden="true" />
            {predicting ? 'Generating…' : 'Generate forecast'}
          </button>

          <button className="btn btn--sm" style={{ marginLeft: 'auto' }} onClick={() => downloadForecastCsv(forecastParams)}>
            <i className="ti ti-file-spreadsheet" aria-hidden="true" />
            CSV
          </button>
        </div>

        {actionMessage && (
          <div style={{ fontSize: 13.5, color: 'var(--positive)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <i className="ti ti-circle-check" aria-hidden="true" />
            {actionMessage}
          </div>
        )}
        {actionError && <ErrorBanner message={actionError.message} />}

        {deployedModel && (
          <div style={{ marginTop: 14, display: 'flex', gap: 18, fontSize: 13, color: 'var(--text-secondary)' }} className="mono">
            <span>Model: <strong style={{ color: 'var(--text-primary)' }}>{deployedModel.modelName}</strong></span>
            <span>MAPE: <strong style={{ color: 'var(--text-primary)' }}>{deployedModel.mape ? formatPercent(Number(deployedModel.mape), { showSign: false }) : '—'}</strong></span>
            <span>Trained: {deployedModel.trainedAt ? new Date(deployedModel.trainedAt).toLocaleDateString() : '—'}</span>
          </div>
        )}
      </div>

      <div className="card section">
        <div className="card__header">
          <h2 className="card__title">Forecast vs actuals</h2>
        </div>
        {forecasts.loading && <Spinner label="Loading forecast…" />}
        {forecasts.error && <ErrorBanner message={forecasts.error.message} onRetry={forecasts.refetch} />}
        {!forecasts.loading && !forecasts.error && chartData.length === 0 && (
          <EmptyState
            icon="telescope"
            title="No forecast yet"
            description="Train a model and generate a forecast for this segment to see results here."
          />
        )}
        {!forecasts.loading && !forecasts.error && chartData.length > 0 && <ForecastChart data={chartData} />}
      </div>

      <InsightPanel
        title="Forecast explanation"
        icon="message-chatbot"
        loading={explainLoading}
        error={explainError}
        result={explainResult}
        onRegenerate={runExplain}
      />
    </>
  )
}
