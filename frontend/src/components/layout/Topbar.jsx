import React from 'react'
import { useFilters } from '../../context/FilterContext'
import './Topbar.css'

export default function Topbar({ title, description }) {
  const { segmentLabel } = useFilters()

  return (
    <header className="topbar">
      <div>
        <h1 className="topbar__title">{title}</h1>
        {description && <p className="topbar__description">{description}</p>}
      </div>
      <div className="topbar__segment">
        <i className="ti ti-filter" aria-hidden="true" />
        {segmentLabel}
      </div>
    </header>
  )
}
