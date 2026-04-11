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

export const CtaScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Logo entrance
  const logoProgress = spring({
    frame,
    fps,
    config: { damping: 14 },
  });

  // Title
  const titleProgress = spring({
    frame: frame - Math.round(0.4 * fps),
    fps,
    config: { damping: 18 },
  });

  // Subtitle
  const subProgress = spring({
    frame: frame - Math.round(0.9 * fps),
    fps,
    config: { damping: 18 },
  });

  // CTA button
  const buttonProgress = spring({
    frame: frame - Math.round(1.4 * fps),
    fps,
    config: { damping: 12 },
  });

  // URL text
  const urlOpacity = interpolate(frame, [2 * fps, 2.6 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Accent line
  const lineWidth = interpolate(frame, [0.5 * fps, 2 * fps], [0, 300], {
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
      {/* Subtle background glow */}
      <div
        style={{
          position: 'absolute',
          width: 600,
          height: 600,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${COLORS.purple}10 0%, transparent 70%)`,
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
        }}
      />

      {/* Content */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 28,
        }}
      >
        {/* Logo */}
        <div
          style={{
            transform: `scale(${logoProgress})`,
            opacity: logoProgress,
          }}
        >
          <Img
            src={staticFile('logo.png')}
            style={{
              width: 100,
              height: 100,
              borderRadius: 20,
            }}
          />
        </div>

        {/* Title */}
        <div
          style={{
            opacity: titleProgress,
            transform: `translateY(${interpolate(titleProgress, [0, 1], [30, 0])}px)`,
            fontSize: 56,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
            textAlign: 'center',
          }}
        >
          Ready to start?
        </div>

        {/* Subtitle */}
        <div
          style={{
            opacity: subProgress,
            transform: `translateY(${interpolate(subProgress, [0, 1], [20, 0])}px)`,
            fontSize: 22,
            color: COLORS.textSecondary,
            fontFamily: FONT_FAMILY,
            textAlign: 'center',
            maxWidth: 500,
          }}
        >
          Stop drowning in filings. Get clarity in minutes.
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

        {/* CTA Button */}
        <div
          style={{
            transform: `scale(${buttonProgress})`,
            opacity: buttonProgress,
            background: `linear-gradient(135deg, ${COLORS.purple}, ${COLORS.cyan})`,
            borderRadius: 50,
            padding: '18px 56px',
            fontSize: 20,
            fontWeight: 700,
            color: '#fff',
            fontFamily: FONT_FAMILY,
            letterSpacing: 0.5,
            marginTop: 8,
          }}
        >
          Get Started Free
        </div>

        {/* URL */}
        <div
          style={{
            opacity: urlOpacity,
            fontSize: 24,
            fontWeight: 700,
            fontFamily: FONT_FAMILY,
            color: COLORS.textSecondary,
            letterSpacing: 1.5,
            marginTop: 4,
          }}
        >
          financesums.com
        </div>
      </div>
    </AbsoluteFill>
  );
};
