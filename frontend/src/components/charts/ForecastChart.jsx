import React from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { formatCurrency, formatDate } from '../../utils/format'

export default function ForecastChart({ data, height = 340 }) {
  if (!data || data.length === 0) return null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="ciBand" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3d7a6e" stopOpacity={0.18} />
            <stop offset="100%" stopColor="#3d7a6e" stopOpacity={0.04} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(v) => formatDate(v)}
          tick={{ fontSize: 12, fill: 'var(--text-tertiary)' }}
          axisLine={{ stroke: 'var(--border-subtle)' }}
          tickLine={false}
          minTickGap={28}
        />
        <YAxis
          tickFormatter={(v) => formatCurrency(v, { compact: true })}
          tick={{ fontSize: 12, fill: 'var(--text-tertiary)' }}
          axisLine={false}
          tickLine={false}
          width={64}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 10,
            fontSize: 13,
            boxShadow: 'var(--shadow-popover)',
          }}
          labelFormatter={(v) => formatDate(v, { format: 'long' })}
          formatter={(value, name) => [formatCurrency(value), name]}
        />
        <Legend wrapperStyle={{ fontSize: 12.5 }} />
        <Area
          type="monotone"
          dataKey="upper80"
          stroke="none"
          fill="url(#ciBand)"
          name="80% confidence (upper)"
          legendType="none"
        />
        <Area
          type="monotone"
          dataKey="lower80"
          stroke="none"
          fill="var(--bg-surface)"
          fillOpacity={1}
          name="80% confidence (lower)"
          legendType="none"
        />
        <Line
          type="monotone"
          dataKey="predicted"
          stroke="#c1822a"
          strokeWidth={2.25}
          dot={false}
          name="Forecast"
        />
        <Line
          type="monotone"
          dataKey="actual"
          stroke="#1d1812"
          strokeWidth={2}
          strokeDasharray="4 3"
          dot={{ r: 2.5 }}
          name="Actual"
          connectNulls
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
