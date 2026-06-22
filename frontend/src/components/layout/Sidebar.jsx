import React from 'react'
import { NavLink } from 'react-router-dom'
import { useFilters } from '../../context/FilterContext'
import './Sidebar.css'

const NAV_ITEMS = [
  { to: '/', label: 'Revenue overview', icon: 'chart-area-line', end: true },
  { to: '/categories', label: 'Category analysis', icon: 'category-2' },
  { to: '/regions', label: 'Regional analysis', icon: 'map-2' },
  { to: '/forecast', label: 'Forecast explorer', icon: 'telescope' },
  { to: '/insights', label: 'AI insights', icon: 'bulb' },
  { to: '/ask', label: 'Ask AI', icon: 'message-2' },
]

export default function Sidebar() {
  const { categories, regions, categoryId, regionId, setCategoryId, setRegionId, lookbackDays, setLookbackDays } =
    useFilters()

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__brand-mark">CPG</div>
        <div>
          <div className="sidebar__brand-name">CPG Platform</div>
          <div className="sidebar__brand-sub">Revenue intelligence</div>
        </div>
      </div>

      <nav className="sidebar__nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `sidebar__link${isActive ? ' sidebar__link--active' : ''}`}
          >
            <i className={`ti ti-${item.icon}`} aria-hidden="true" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar__filters">
        <div className="sidebar__filters-title">Segment</div>

        <label className="sidebar__field">
          <span>Category</span>
          <select value={categoryId ?? ''} onChange={(e) => setCategoryId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.categoryId} value={c.categoryId}>
                {c.categoryName}
              </option>
            ))}
          </select>
        </label>

        <label className="sidebar__field">
          <span>Region</span>
          <select value={regionId ?? ''} onChange={(e) => setRegionId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">All regions</option>
            {regions.map((r) => (
              <option key={r.regionId} value={r.regionId}>
                {r.regionName}
              </option>
            ))}
          </select>
        </label>

        <label className="sidebar__field">
          <span>Lookback window</span>
          <select value={lookbackDays} onChange={(e) => setLookbackDays(Number(e.target.value))}>
            <option value={30}>30 days</option>
            <option value={60}>60 days</option>
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
            <option value={365}>365 days</option>
          </select>
        </label>
      </div>

      <div className="sidebar__footer">
        <i className="ti ti-circle-filled sidebar__status-dot" aria-hidden="true" />
        Connected to FastAPI
      </div>
    </aside>
  )
}
