'use client'

import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface FinancialChartsProps {
  ratios: Record<string, number | null>
}

export default function FinancialCharts({ ratios }: FinancialChartsProps) {
  // Format data for charts
  const profitabilityData = [
    { name: 'Gross Margin', value: ratios.gross_margin ? ratios.gross_margin * 100 : 0 },
    { name: 'Operating Margin', value: ratios.operating_margin ? ratios.operating_margin * 100 : 0 },
    { name: 'Net Margin', value: ratios.net_margin ? ratios.net_margin * 100 : 0 },
  ]

  const liquidityData = [
    { name: 'Current Ratio', value: ratios.current_ratio || 0 },
    { name: 'Quick Ratio', value: ratios.quick_ratio || 0 },
  ]

  const leverageData = [
    { name: 'Debt to Equity', value: ratios.debt_to_equity || 0 },
    { name: 'Net Debt/EBITDA', value: ratios.net_debt_to_ebitda || 0 },
    { name: 'Interest Coverage', value: ratios.interest_coverage || 0 },
  ]

  return (
    <div className="space-y-8">
      {/* Profitability Chart */}
      <div className="bg-white p-6 rounded-lg shadow">
        <h3 className="text-lg font-semibold mb-4">Profitability Margins (%)</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={profitabilityData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip formatter={(value: any) => `${value.toFixed(2)}%`} />
            <Bar dataKey="value" fill="#0ea5e9" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Liquidity Chart */}
      <div className="bg-white p-6 rounded-lg shadow">
        <h3 className="text-lg font-semibold mb-4">Liquidity Ratios</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={liquidityData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip formatter={(value: any) => value.toFixed(2)} />
            <Bar dataKey="value" fill="#10b981" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Leverage Chart */}
      <div className="bg-white p-6 rounded-lg shadow">
        <h3 className="text-lg font-semibold mb-4">Leverage & Solvency</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={leverageData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip formatter={(value: any) => value.toFixed(2)} />
            <Bar dataKey="value" fill="#f59e0b" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}










