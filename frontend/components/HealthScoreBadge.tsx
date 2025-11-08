'use client'

interface HealthScoreBadgeProps {
  score: number
  band: string
}

export default function HealthScoreBadge({ score, band }: HealthScoreBadgeProps) {
  const getColorClass = () => {
    if (score >= 85) return 'bg-green-100 text-green-800 border-green-300'
    if (score >= 70) return 'bg-blue-100 text-blue-800 border-blue-300'
    if (score >= 50) return 'bg-yellow-100 text-yellow-800 border-yellow-300'
    return 'bg-red-100 text-red-800 border-red-300'
  }

  const getGradientClass = () => {
    if (score >= 85) return 'from-green-500 to-green-600'
    if (score >= 70) return 'from-blue-500 to-blue-600'
    if (score >= 50) return 'from-yellow-500 to-yellow-600'
    return 'from-red-500 to-red-600'
  }

  return (
    <div className={`inline-flex items-center px-6 py-3 rounded-lg border-2 ${getColorClass()}`}>
      <div className="flex items-center space-x-4">
        <div className={`text-4xl font-bold bg-gradient-to-r ${getGradientClass()} bg-clip-text text-transparent`}>
          {score.toFixed(1)}
        </div>
        <div>
          <div className="text-sm font-semibold">Health Score</div>
          <div className="text-xs font-medium">{band}</div>
        </div>
      </div>
    </div>
  )
}










