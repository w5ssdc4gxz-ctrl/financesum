import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { COLORS, FONT_FAMILY } from '../constants';

const FILING_LINES = [
  'UNITED STATES SECURITIES AND EXCHANGE COMMISSION',
  'Washington, D.C. 20549',
  'FORM 10-K',
  'ANNUAL REPORT PURSUANT TO SECTION 13',
  'For the fiscal year ended December 31, 2025',
  'Commission file number: 001-37580',
  'Item 1. Business Overview',
  'Item 1A. Risk Factors',
  'Item 2. Properties',
  'Item 3. Legal Proceedings',
  'Item 6. Selected Financial Data',
  'Item 7. Management Discussion & Analysis',
  'Item 8. Financial Statements',
  'Item 9. Changes and Disagreements',
  'Item 10. Directors, Executive Officers',
  'Item 11. Executive Compensation',
  'Item 12. Security Ownership',
  'Item 13. Certain Relationships',
  'Item 14. Principal Accountant Fees',
  'Item 15. Exhibit and Financial Statement',
];

export const ProblemScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Title animation
  const titleProgress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 100 },
  });

  // Subtitle
  const subOpacity = interpolate(frame, [0.6 * fps, 1.2 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Filing text scroll - full height, both columns
  const scrollY = interpolate(frame, [0.3 * fps, 4.5 * fps], [0, -500], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Red stress tint
  const stressOpacity = interpolate(frame, [2 * fps, 4.5 * fps], [0, 0.2], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Stats
  const stats = [
    { label: '10-K filings average', value: '200+', unit: 'pages', delay: 1.6 },
    { label: 'Analyst reading time', value: '8+', unit: 'hours', delay: 2.1 },
    { label: 'Key insights buried in', value: 'Legal', unit: 'jargon', delay: 2.6 },
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.darkerBg }}>
      {/* Background scrolling filing text - LEFT column */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: '35%',
          height: '100%',
          overflow: 'hidden',
          opacity: 0.06,
        }}
      >
        <div style={{ transform: `translateY(${scrollY}px)`, padding: '40px 40px' }}>
          {[...FILING_LINES, ...FILING_LINES, ...FILING_LINES].map((line, i) => (
            <div
              key={i}
              style={{
                fontSize: 13,
                color: COLORS.textPrimary,
                fontFamily: 'monospace',
                lineHeight: 2.4,
                whiteSpace: 'nowrap',
              }}
            >
              {line}
            </div>
          ))}
        </div>
      </div>

      {/* Background scrolling filing text - RIGHT column (scrolls opposite) */}
      <div
        style={{
          position: 'absolute',
          right: 0,
          top: 0,
          width: '35%',
          height: '100%',
          overflow: 'hidden',
          opacity: 0.06,
        }}
      >
        <div style={{ transform: `translateY(${-scrollY - 200}px)`, padding: '40px 40px' }}>
          {[...FILING_LINES, ...FILING_LINES, ...FILING_LINES].map((line, i) => (
            <div
              key={i}
              style={{
                fontSize: 13,
                color: COLORS.textPrimary,
                fontFamily: 'monospace',
                lineHeight: 2.4,
                whiteSpace: 'nowrap',
              }}
            >
              {line}
            </div>
          ))}
        </div>
      </div>

      {/* Stress red vignette */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: `radial-gradient(ellipse at center, transparent 40%, ${COLORS.red})`,
          opacity: stressOpacity,
        }}
      />

      {/* Main content - centered, full width */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
          padding: '80px 120px',
        }}
      >
        {/* Title */}
        <div
          style={{
            opacity: titleProgress,
            transform: `translateY(${interpolate(titleProgress, [0, 1], [40, 0])}px)`,
            fontSize: 64,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
            textAlign: 'center',
            marginBottom: 20,
            lineHeight: 1.1,
          }}
        >
          Drowning in SEC filings?
        </div>

        {/* Subtitle */}
        <div
          style={{
            opacity: subOpacity,
            fontSize: 24,
            color: COLORS.textSecondary,
            fontFamily: FONT_FAMILY,
            textAlign: 'center',
            marginBottom: 80,
            maxWidth: 700,
            lineHeight: 1.5,
          }}
        >
          10-Ks, 10-Qs, earnings calls — hundreds of pages of dense financial data.
        </div>

        {/* Stats row - full width */}
        <div style={{ display: 'flex', gap: 50, width: '100%', justifyContent: 'center' }}>
          {stats.map((stat, i) => {
            const cardProgress = spring({
              frame: frame - Math.round(stat.delay * fps),
              fps,
              config: { damping: 14 },
            });
            return (
              <div
                key={i}
                style={{
                  transform: `scale(${cardProgress}) translateY(${interpolate(cardProgress, [0, 1], [30, 0])}px)`,
                  opacity: cardProgress,
                  background: COLORS.cardBg,
                  border: `1px solid ${COLORS.red}30`,
                  borderRadius: 20,
                  padding: '36px 48px',
                  textAlign: 'center',
                  flex: 1,
                  maxWidth: 320,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 6 }}>
                  <span
                    style={{
                      fontSize: 48,
                      fontWeight: 800,
                      color: COLORS.red,
                      fontFamily: FONT_FAMILY,
                    }}
                  >
                    {stat.value}
                  </span>
                  <span
                    style={{
                      fontSize: 22,
                      fontWeight: 600,
                      color: COLORS.red,
                      fontFamily: FONT_FAMILY,
                      opacity: 0.7,
                    }}
                  >
                    {stat.unit}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 15,
                    color: COLORS.textMuted,
                    fontFamily: FONT_FAMILY,
                    marginTop: 10,
                  }}
                >
                  {stat.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
