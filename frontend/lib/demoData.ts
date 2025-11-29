// Demo/sample data generators for dashboard visualizations

import type { HeatmapDataPoint } from '@/components/dashboard/charts/ActivityHeatmap'
import type { EnhancedBarDataPoint } from '@/components/dashboard/charts/EnhancedBarChart'
import type { DonutDataPoint } from '@/components/dashboard/ui/DonutChart'

// Generate activity heatmap data for the past N weeks
export function generateDemoHeatmapData(weeks: number = 12): HeatmapDataPoint[] {
  const data: HeatmapDataPoint[] = []
  const endDate = new Date()
  const startDate = new Date()
  startDate.setDate(endDate.getDate() - weeks * 7)

  for (let d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
    const dateStr = d.toISOString().split('T')[0]
    // Random count with some days having no activity
    const count = Math.random() > 0.3 ? Math.floor(Math.random() * 8) : 0
    data.push({ date: dateStr, count })
  }

  return data
}

// Generate analysis trend data for the past 8 days
export function generateDemoTrendData() {
  const data = []
  const today = new Date()

  for (let i = 7; i >= 0; i--) {
    const date = new Date(today)
    date.setDate(today.getDate() - i)
    data.push({
      date,
      label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      value: Math.floor(Math.random() * 5) + 1
    })
  }

  return data
}

// Generate sector distribution data
export function generateDemoSectorData(): { sectors: DonutDataPoint[]; barData: EnhancedBarDataPoint[] } {
  const sectorData = [
    { label: 'Technology', value: 12, color: '#3b82f6' },
    { label: 'Healthcare', value: 8, color: '#10b981' },
    { label: 'Finance', value: 7, color: '#8b5cf6' },
    { label: 'Consumer', value: 6, color: '#f59e0b' },
    { label: 'Energy', value: 4, color: '#ec4899' },
    { label: 'Industrial', value: 3, color: '#06b6d4' }
  ]

  return {
    sectors: sectorData,
    barData: sectorData.slice(0, 5).map(s => ({
      name: s.label,
      value: s.value,
      color: s.color
    }))
  }
}

// Generate regional distribution data
export function generateDemoRegionData() {
  return [
    { label: 'North America', value: 15, icon: '🌎' },
    { label: 'Europe', value: 12, icon: '🌍' },
    { label: 'Asia Pacific', value: 10, icon: '🌏' },
    { label: 'Latin America', value: 3, icon: '🌎' },
    { label: 'Middle East', value: 2, icon: '🌍' }
  ]
}

// Generate top companies data
export function generateDemoCompaniesData(): EnhancedBarDataPoint[] {
  return [
    { name: 'Apple Inc.', ticker: 'AAPL', value: 87, color: '#3b82f6' },
    { name: 'Microsoft', ticker: 'MSFT', value: 85, color: '#10b981' },
    { name: 'Amazon', ticker: 'AMZN', value: 78, color: '#8b5cf6' },
    { name: 'Alphabet', ticker: 'GOOGL', value: 82, color: '#f59e0b' },
    { name: 'Tesla', ticker: 'TSLA', value: 72, color: '#ec4899' }
  ]
}

// Generate recent analyses
export function generateDemoRecentAnalyses() {
  const companies = [
    { name: 'Apple Inc.', ticker: 'AAPL', score: 87, sector: 'Technology' },
    { name: 'Microsoft Corp.', ticker: 'MSFT', score: 85, sector: 'Technology' },
    { name: 'Johnson & Johnson', ticker: 'JNJ', score: 78, sector: 'Healthcare' },
    { name: 'JPMorgan Chase', ticker: 'JPM', score: 82, sector: 'Finance' },
    { name: 'Tesla Inc.', ticker: 'TSLA', score: 72, sector: 'Consumer' },
    { name: 'Pfizer Inc.', ticker: 'PFE', score: 76, sector: 'Healthcare' }
  ]

  const now = Date.now()
  return companies.map((company, idx) => ({
    ...company,
    analysisId: `demo-${idx}`,
    companyId: `comp-${idx}`,
    generatedAt: new Date(now - idx * 3600000 * 4).toISOString(), // 4 hours apart
    summaryPreview: `Comprehensive financial analysis of ${company.name} reveals strong fundamentals with positive outlook.`
  }))
}

// Check if we should use demo data (when user has minimal real data)
export function shouldUseDemoData(realDataCount: number, threshold: number = 3): boolean {
  return realDataCount < threshold
}

// Merge real data with demo data to ensure minimum content
export function enrichWithDemoData<T>(
  realData: T[],
  demoData: T[],
  minCount: number = 5
): T[] {
  if (realData.length >= minCount) {
    return realData
  }

  // Take real data first, then fill with demo data
  const needed = minCount - realData.length
  return [...realData, ...demoData.slice(0, needed)]
}

// Get demo stats
export function generateDemoStats() {
  return {
    analysisCount: 42,
    avgScore: 78,
    sectors: generateDemoSectorData().sectors,
    countries: generateDemoRegionData(),
    companiesAnalyzed: 35,
    lastAnalysis: new Date(Date.now() - 2 * 3600000).toISOString() // 2 hours ago
  }
}
