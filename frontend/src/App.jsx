import React from 'react'
import { HashRouter, Routes, Route } from 'react-router-dom'
import AppShell from './components/layout/AppShell'
import { FilterProvider } from './context/FilterContext'
import RevenueOverview from './pages/RevenueOverview'
import CategoryAnalysis from './pages/CategoryAnalysis'
import RegionalAnalysis from './pages/RegionalAnalysis'
import ForecastExplorer from './pages/ForecastExplorer'
import AiInsights from './pages/AiInsights'
import AskAi from './pages/AskAi'
import './styles/components.css'

export default function App() {
  return (
    <FilterProvider>
      <HashRouter>
        <AppShell>
          <Routes>
            <Route path="/"           element={<RevenueOverview />} />
            <Route path="/categories" element={<CategoryAnalysis />} />
            <Route path="/regions"    element={<RegionalAnalysis />} />
            <Route path="/forecast"   element={<ForecastExplorer />} />
            <Route path="/insights"   element={<AiInsights />} />
            <Route path="/ask"        element={<AskAi />} />
          </Routes>
        </AppShell>
      </HashRouter>
    </FilterProvider>
  )
}
