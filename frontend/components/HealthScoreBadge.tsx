'use client'

interface HealthScoreBadgeProps {
  score: number
  band: string
  size?: 'sm' | 'md' | 'lg'
}

export default function HealthScoreBadge({ score, band, size = 'md' }: HealthScoreBadgeProps) {
  const getColorClass = () => {
    if (score >= 85) return 'bg-green-400 text-black'
    if (score >= 70) return 'bg-blue-400 text-black'
    if (score >= 50) return 'bg-yellow-400 text-black'
    return 'bg-red-400 text-black'
  }

  const sizeClasses = {
    sm: 'px-3 py-1 text-xs',
    md: 'px-4 py-2 text-sm',
    lg: 'px-6 py-3 text-base'
  }

  const scoreSizeClasses = {
    sm: 'text-lg',
    md: 'text-2xl',
    lg: 'text-4xl'
  }

  return (
    <div className={`inline-flex items-center border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] ${getColorClass()} ${sizeClasses[size]}`}>
      <div className="flex items-center space-x-3">
        <div className={`font-black ${scoreSizeClasses[size]}`}>
          {score.toFixed(1)}
        </div>
        <div className="border-l-2 border-black pl-3">
          <div className="font-bold uppercase leading-none mb-1">Health Score</div>
          <div className="font-mono text-xs font-bold uppercase">{band}</div>
        </div>
      </div>
    </div>
  )
}
















