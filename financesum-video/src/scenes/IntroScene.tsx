import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { COLORS, FONT_FAMILY } from '../constants';

export const IntroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Logo scale entrance
  const logoScale = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 80 },
  });

  const logoOpacity = interpolate(frame, [0, 0.4 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Title slide up
  const titleProgress = spring({
    frame: frame - Math.round(0.6 * fps),
    fps,
    config: { damping: 18, stiffness: 100 },
  });
  const titleY = interpolate(titleProgress, [0, 1], [50, 0]);
  const titleOpacity = interpolate(titleProgress, [0, 1], [0, 1]);

  // Subtitle slide up
  const subProgress = spring({
    frame: frame - Math.round(1.2 * fps),
    fps,
    config: { damping: 18, stiffness: 100 },
  });
  const subY = interpolate(subProgress, [0, 1], [40, 0]);
  const subOpacity = interpolate(subProgress, [0, 1], [0, 1]);

  // Badge
  const badgeProgress = spring({
    frame: frame - Math.round(2.2 * fps),
    fps,
    config: { damping: 14 },
  });

  // Subtle bottom line accent
  const lineWidth = interpolate(frame, [1 * fps, 2.5 * fps], [0, 400], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.darkerBg,
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      {/* Subtle radial glow behind content */}
      <div
        style={{
          position: 'absolute',
          width: 800,
          height: 800,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${COLORS.purple}12 0%, transparent 70%)`,
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
        }}
      />

      {/* Content stack */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 32,
        }}
      >
        {/* Real logo */}
        <div
          style={{
            transform: `scale(${logoScale})`,
            opacity: logoOpacity,
          }}
        >
          <Img
            src={staticFile('logo.png')}
            style={{
              width: 120,
              height: 120,
              borderRadius: 24,
            }}
          />
        </div>

        {/* Title */}
        <div
          style={{
            opacity: titleOpacity,
            transform: `translateY(${titleY}px)`,
          }}
        >
          <span
            style={{
              fontSize: 80,
              fontWeight: 800,
              fontFamily: FONT_FAMILY,
              background: `linear-gradient(135deg, ${COLORS.purple}, ${COLORS.cyan})`,
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              letterSpacing: -3,
            }}
          >
            FinanceSum
          </span>
        </div>

        {/* Accent line */}
        <div
          style={{
            width: lineWidth,
            height: 2,
            background: `linear-gradient(90deg, transparent, ${COLORS.purple}, ${COLORS.cyan}, transparent)`,
            borderRadius: 1,
          }}
        />

        {/* Subtitle */}
        <div
          style={{
            opacity: subOpacity,
            transform: `translateY(${subY}px)`,
            fontSize: 28,
            color: COLORS.textSecondary,
            fontFamily: FONT_FAMILY,
            fontWeight: 400,
            textAlign: 'center',
            letterSpacing: 1,
          }}
        >
          Financial analysis, reimagined.
        </div>

        {/* Badge */}
        <div
          style={{
            transform: `scale(${badgeProgress})`,
            opacity: badgeProgress,
            background: `${COLORS.purple}18`,
            border: `1px solid ${COLORS.purple}40`,
            borderRadius: 100,
            padding: '10px 28px',
            fontSize: 13,
            color: COLORS.cyan,
            fontFamily: FONT_FAMILY,
            fontWeight: 600,
            letterSpacing: 2,
            textTransform: 'uppercase',
          }}
        >
          Now in Public Beta
        </div>
      </div>
    </AbsoluteFill>
  );
};
