import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { COLORS, FONT_FAMILY } from '../constants';

export const SolutionScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Title
  const titleProgress = spring({
    frame,
    fps,
    config: { damping: 18 },
  });

  // Left panel slide in
  const leftProgress = spring({
    frame: frame - Math.round(0.3 * fps),
    fps,
    config: { damping: 16 },
  });

  // Arrow
  const arrowProgress = spring({
    frame: frame - Math.round(1.2 * fps),
    fps,
    config: { damping: 14 },
  });

  // Right panel slide in
  const rightProgress = spring({
    frame: frame - Math.round(0.8 * fps),
    fps,
    config: { damping: 16 },
  });

  // Bottom text
  const bottomOpacity = interpolate(frame, [3 * fps, 3.8 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const filingLines = [
    'Item 7. Management Discussion',
    'Revenue decreased by 2.3% compared...',
    'Operating expenses include depreciation...',
    'Liquidity and Capital Resources...',
    'Risk factors pertaining to market...',
    'Forward-looking statements involve...',
  ];

  const memoLines = [
    { label: 'Revenue', text: '$42.3B (-2.3% YoY)', color: COLORS.textPrimary },
    { label: 'Health Score', text: '72 / 100', color: COLORS.amber },
    { label: 'Key Risk', text: 'Market volatility', color: COLORS.textPrimary },
    { label: 'Verdict', text: 'Hold with caution', color: COLORS.green },
  ];

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.darkerBg,
        justifyContent: 'center',
        alignItems: 'center',
        padding: '80px 100px',
      }}
    >
      {/* Title - top area */}
      <div
        style={{
          position: 'absolute',
          top: 80,
          left: 0,
          right: 0,
          textAlign: 'center',
          opacity: titleProgress,
          transform: `translateY(${interpolate(titleProgress, [0, 1], [30, 0])}px)`,
        }}
      >
        <div
          style={{
            fontSize: 52,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
          }}
        >
          From 200 pages to{' '}
          <span style={{ color: COLORS.green }}>one clear memo</span>
        </div>
      </div>

      {/* Cards row - centered vertically */}
      <div
        style={{
          display: 'flex',
          alignItems: 'stretch',
          gap: 60,
          width: '100%',
          maxWidth: 1500,
          marginTop: 40,
        }}
      >
        {/* Left: SEC Filing */}
        <div
          style={{
            flex: 1,
            transform: `translateX(${interpolate(leftProgress, [0, 1], [-80, 0])}px)`,
            opacity: leftProgress,
            background: COLORS.cardBg,
            borderRadius: 20,
            padding: '40px 44px',
            border: `1px solid ${COLORS.textMuted}20`,
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: COLORS.textMuted,
              fontFamily: FONT_FAMILY,
              marginBottom: 6,
              textTransform: 'uppercase',
              letterSpacing: 2,
              fontWeight: 600,
            }}
          >
            SEC 10-K Filing
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: COLORS.textPrimary,
              fontFamily: FONT_FAMILY,
              marginBottom: 24,
            }}
          >
            Apple Inc. (AAPL)
          </div>
          {filingLines.map((line, i) => (
            <div
              key={i}
              style={{
                fontSize: 14,
                color: COLORS.textMuted,
                fontFamily: 'monospace',
                lineHeight: 2.2,
                borderBottom: i < filingLines.length - 1 ? `1px solid ${COLORS.textMuted}12` : 'none',
              }}
            >
              {line}
            </div>
          ))}
          <div
            style={{
              marginTop: 16,
              fontSize: 13,
              color: COLORS.red,
              fontFamily: FONT_FAMILY,
              fontWeight: 600,
              opacity: 0.8,
            }}
          >
            ... 195 more pages
          </div>
        </div>

        {/* Arrow in center */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 12,
            transform: `scale(${arrowProgress})`,
            opacity: arrowProgress,
          }}
        >
          <svg width={64} height={64} viewBox="0 0 64 64">
            <circle cx={32} cy={32} r={30} fill="none" stroke={COLORS.textMuted} strokeWidth={1.5} opacity={0.3} />
            <path
              d="M20 32 H40 M34 24 L42 32 L34 40"
              fill="none"
              stroke={COLORS.green}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <div
            style={{
              fontSize: 11,
              color: COLORS.textMuted,
              fontFamily: FONT_FAMILY,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: 1.5,
            }}
          >
            AI Analysis
          </div>
        </div>

        {/* Right: AI Memo */}
        <div
          style={{
            flex: 1,
            transform: `translateX(${interpolate(rightProgress, [0, 1], [80, 0])}px)`,
            opacity: rightProgress,
            background: COLORS.cardBg,
            borderRadius: 20,
            padding: '40px 44px',
            border: `1px solid ${COLORS.green}25`,
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: COLORS.green,
              fontFamily: FONT_FAMILY,
              marginBottom: 6,
              textTransform: 'uppercase',
              letterSpacing: 2,
              fontWeight: 600,
            }}
          >
            Executive Memo
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: COLORS.textPrimary,
              fontFamily: FONT_FAMILY,
              marginBottom: 24,
            }}
          >
            Apple Inc. Summary
          </div>
          {memoLines.map((line, i) => {
            const lineOpacity = interpolate(
              frame,
              [(1.5 + i * 0.4) * fps, (2.0 + i * 0.4) * fps],
              [0, 1],
              { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
            );
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                  fontSize: 16,
                  fontFamily: FONT_FAMILY,
                  lineHeight: 2.6,
                  opacity: lineOpacity,
                }}
              >
                <span style={{ color: COLORS.green, fontWeight: 700, fontSize: 14 }}>{'\u2713'}</span>
                <span style={{ color: COLORS.textMuted, fontWeight: 600, minWidth: 100 }}>{line.label}</span>
                <span style={{ color: line.color }}>{line.text}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Bottom text */}
      <div
        style={{
          position: 'absolute',
          bottom: 70,
          opacity: bottomOpacity,
          fontSize: 18,
          color: COLORS.textMuted,
          fontFamily: FONT_FAMILY,
          textAlign: 'center',
          letterSpacing: 0.5,
        }}
      >
        Powered by Google Gemini AI + SEC EDGAR data
      </div>
    </AbsoluteFill>
  );
};
