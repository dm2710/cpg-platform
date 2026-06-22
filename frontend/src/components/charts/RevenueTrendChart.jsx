import React from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { formatCurrency, formatDate } from '../../utils/format'

export default function RevenueTrendChart({ data, height = 280 }) {
  if (!data || data.length === 0) return null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#c1822a" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#c1822a" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
        <XAxis
          dataKey="period"
          tickFormatter={(v) => formatDate(v)}
          tick={{ fontSize: 12, fill: 'var(--text-tertiary)' }}
          axisLine={{ stroke: 'var(--border-subtle)' }}
          tickLine={false}
          minTickGap={32}
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
          formatter={(value) => [formatCurrency(value), 'Revenue']}
        />
        <Area
          type="monotone"
          dataKey="revenue"
          stroke="#c1822a"
          strokeWidth={2}
          fill="url(#revenueFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
