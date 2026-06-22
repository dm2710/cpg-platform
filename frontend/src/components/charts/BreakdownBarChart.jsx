import React from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import { formatCurrency } from '../../utils/format'

const PALETTE = ['#c1822a', '#3d7a6e', '#b54c44', '#756347', '#a06a1f', '#2e5e54']

export default function BreakdownBarChart({ data, dataKey = 'revenue', labelKey = 'label', height = 280, onBarClick }) {
  if (!data || data.length === 0) return null

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" horizontal={false} />
        <XAxis
          type="number"
          tickFormatter={(v) => formatCurrency(v, { compact: true })}
          tick={{ fontSize: 12, fill: 'var(--text-tertiary)' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey={labelKey}
          tick={{ fontSize: 13, fill: 'var(--text-primary)' }}
          axisLine={false}
          tickLine={false}
          width={120}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 10,
            fontSize: 13,
            boxShadow: 'var(--shadow-popover)',
          }}
          formatter={(value) => [formatCurrency(value), 'Revenue']}
          cursor={{ fill: 'rgba(0,0,0,0.03)' }}
        />
        <Bar dataKey={dataKey} radius={[0, 6, 6, 0]} maxBarSize={28} onClick={onBarClick} cursor={onBarClick ? 'pointer' : 'default'}>
          {data.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
