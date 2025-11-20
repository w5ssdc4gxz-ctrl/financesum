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

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white dark:bg-black border-2 border-black dark:border-white p-3 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
          <p className="font-black uppercase text-xs mb-2">{label}</p>
          <p className="font-mono text-sm font-bold">
            {payload[0].value.toFixed(2)}
            {payload[0].payload.name.includes('Margin') ? '%' : ''}
          </p>
        </div>
      )
    }
    return null
  }

  return (
    <div className="space-y-8">
      {/* Profitability Chart */}
      <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
        <h3 className="text-lg font-black uppercase mb-6 flex items-center gap-3">
          <span className="w-4 h-4 bg-blue-600"></span>
          Profitability Margins (%)
        </h3>
        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={profitabilityData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.1} vertical={false} />
              <XAxis
                dataKey="name"
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontWeight: 700 }}
                dy={10}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontFamily: 'monospace' }}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(37, 99, 235, 0.1)' }} />
              <Bar
                dataKey="value"
                fill="#2563EB"
                radius={[0, 0, 0, 0]}
                barSize={40}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Liquidity Chart */}
      <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
        <h3 className="text-lg font-black uppercase mb-6 flex items-center gap-3">
          <span className="w-4 h-4 bg-emerald-500"></span>
          Liquidity Ratios
        </h3>
        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={liquidityData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.1} vertical={false} />
              <XAxis
                dataKey="name"
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontWeight: 700 }}
                dy={10}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontFamily: 'monospace' }}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(16, 185, 129, 0.1)' }} />
              <Bar
                dataKey="value"
                fill="#10B981"
                radius={[0, 0, 0, 0]}
                barSize={40}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Leverage Chart */}
      <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
        <h3 className="text-lg font-black uppercase mb-6 flex items-center gap-3">
          <span className="w-4 h-4 bg-amber-500"></span>
          Leverage & Solvency
        </h3>
        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={leverageData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.1} vertical={false} />
              <XAxis
                dataKey="name"
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontWeight: 700 }}
                dy={10}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 12, fontFamily: 'monospace' }}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(245, 158, 11, 0.1)' }} />
              <Bar
                dataKey="value"
                fill="#F59E0B"
                radius={[0, 0, 0, 0]}
                barSize={40}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
















