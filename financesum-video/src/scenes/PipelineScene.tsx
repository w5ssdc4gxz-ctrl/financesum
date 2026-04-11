import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { COLORS, FONT_FAMILY } from '../constants';

const PIPELINE_STEPS = [
  { label: 'Search', sub: 'SEC EDGAR + EODHD', number: '1' },
  { label: 'Fetch Filing', sub: '10-K / 10-Q data', number: '2' },
  { label: 'AI Analysis', sub: 'Google Gemini 2.5', number: '3' },
  { label: 'Health Score', sub: '16+ financial ratios', number: '4' },
  { label: 'Generate Memo', sub: 'Persona + prefs', number: '5' },
  { label: 'Export', sub: 'PDF / DOCX', number: '6' },
];

export const PipelineScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleProgress = spring({
    frame,
    fps,
    config: { damping: 18 },
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.darkerBg,
        padding: '80px 100px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {/* Title */}
      <div
        style={{
          opacity: titleProgress,
          transform: `translateY(${interpolate(titleProgress, [0, 1], [30, 0])}px)`,
          textAlign: 'center',
          marginBottom: 80,
        }}
      >
        <div
          style={{
            fontSize: 14,
            color: COLORS.textMuted,
            fontFamily: FONT_FAMILY,
            textTransform: 'uppercase',
            letterSpacing: 3,
            marginBottom: 12,
            fontWeight: 600,
          }}
        >
          Under the Hood
        </div>
        <div
          style={{
            fontSize: 48,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
          }}
        >
          The data pipeline
        </div>
      </div>

      {/* Pipeline steps - horizontal row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 0,
          width: '100%',
          justifyContent: 'center',
        }}
      >
        {PIPELINE_STEPS.map((step, i) => {
          const stepDelay = 0.6 + i * 0.4;
          const stepProgress = spring({
            frame: frame - Math.round(stepDelay * fps),
            fps,
            config: { damping: 14 },
          });

          const lineProgress =
            i < PIPELINE_STEPS.length - 1
              ? interpolate(
                  frame,
                  [(stepDelay + 0.3) * fps, (stepDelay + 0.6) * fps],
                  [0, 1],
                  { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
                )
              : 0;

          return (
            <React.Fragment key={i}>
              {/* Step */}
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 14,
                  opacity: stepProgress,
                  transform: `scale(${stepProgress}) translateY(${interpolate(stepProgress, [0, 1], [20, 0])}px)`,
                }}
              >
                {/* Number circle */}
                <div
                  style={{
                    width: 56,
                    height: 56,
                    borderRadius: '50%',
                    background: COLORS.cardBg,
                    border: `2px solid ${COLORS.purple}50`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 22,
                    fontWeight: 800,
                    color: COLORS.purple,
                    fontFamily: FONT_FAMILY,
                  }}
                >
                  {step.number}
                </div>
                {/* Label */}
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: COLORS.textPrimary,
                    fontFamily: FONT_FAMILY,
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {step.label}
                </div>
                {/* Sub text */}
                <div
                  style={{
                    fontSize: 11,
                    color: COLORS.textMuted,
                    fontFamily: FONT_FAMILY,
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {step.sub}
                </div>
              </div>

              {/* Connector line */}
              {i < PIPELINE_STEPS.length - 1 && (
                <div
                  style={{
                    width: 60,
                    height: 2,
                    marginLeft: 12,
                    marginRight: 12,
                    marginBottom: 50,
                    background: COLORS.textMuted,
                    opacity: lineProgress * 0.3,
                    transform: `scaleX(${lineProgress})`,
                    transformOrigin: 'left',
                    borderRadius: 1,
                  }}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Tech badges at bottom */}
      <div
        style={{
          position: 'absolute',
          bottom: 70,
          left: 0,
          right: 0,
          display: 'flex',
          justifyContent: 'center',
          gap: 20,
          opacity: interpolate(frame, [3 * fps, 3.8 * fps], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
        }}
      >
        {['Next.js 14', 'FastAPI', 'Supabase', 'Google Gemini', 'Stripe'].map(
          (tech, i) => (
            <div
              key={i}
              style={{
                fontSize: 12,
                color: COLORS.textSecondary,
                fontFamily: FONT_FAMILY,
                background: COLORS.cardBg,
                border: `1px solid ${COLORS.textMuted}20`,
                borderRadius: 20,
                padding: '8px 18px',
                fontWeight: 500,
              }}
            >
              {tech}
            </div>
          )
        )}
      </div>
    </AbsoluteFill>
  );
};
