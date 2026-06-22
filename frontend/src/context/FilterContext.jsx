import React, { createContext, useContext, useEffect, useState } from 'react'
import { getCategories, getRegions } from '../api/client'

const FilterContext = createContext(null)

export function FilterProvider({ children }) {
  const [categories, setCategories] = useState([])
  const [regions, setRegions] = useState([])
  const [categoryId, setCategoryId] = useState(null)
  const [regionId, setRegionId] = useState(null)
  const [lookbackDays, setLookbackDays] = useState(90)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    Promise.all([getCategories(), getRegions()])
      .then(([cats, regs]) => {
        setCategories(cats || [])
        setRegions(regs || [])
      })
      .catch(() => {})
      .finally(() => setLoaded(true))
  }, [])

  const segmentLabel = (() => {
    const cat = categories.find((c) => c.categoryId === categoryId)?.categoryName
    const reg = regions.find((r) => r.regionId === regionId)?.regionName
    if (!cat && !reg) return 'All segments'
    return [cat || 'All categories', reg || 'All regions'].join(' / ')
  })()

  const value = {
    categories,
    regions,
    categoryId,
    regionId,
    lookbackDays,
    setCategoryId,
    setRegionId,
    setLookbackDays,
    segmentLabel,
    loaded,
  }

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

export function useFilters() {
  const ctx = useContext(FilterContext)
  if (!ctx) throw new Error('useFilters must be used within FilterProvider')
  return ctx
}
