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
import { COLORS, FEATURES, FONT_FAMILY } from '../constants';

export const FeaturesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Each feature gets ~2 seconds
  const FEATURE_DURATION = 2 * fps;
  const activeIndex = Math.min(
    Math.floor(frame / FEATURE_DURATION),
    FEATURES.length - 1
  );
  const localFrame = frame - activeIndex * FEATURE_DURATION;

  // Section title
  const sectionTitleOpacity = interpolate(frame, [0, 0.5 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Current feature animations
  const featureEnter = spring({
    frame: localFrame,
    fps,
    config: { damping: 200 },
  });

  const imageScale = spring({
    frame: localFrame,
    fps,
    config: { damping: 15, stiffness: 80 },
  });

  // Fade out current feature near end of slot
  const featureExit =
    activeIndex < FEATURES.length - 1
      ? interpolate(
          localFrame,
          [FEATURE_DURATION - 10, FEATURE_DURATION],
          [1, 0],
          { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
        )
      : 1;

  const feature = FEATURES[activeIndex];

  // Progress dots
  const dots = FEATURES.map((_, i) => ({
    active: i === activeIndex,
    done: i < activeIndex,
  }));

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(180deg, ${COLORS.darkerBg} 0%, ${COLORS.darkBg} 100%)`,
        padding: 60,
      }}
    >
      {/* Section Title */}
      <div
        style={{
          opacity: sectionTitleOpacity,
          textAlign: 'center',
          marginBottom: 12,
        }}
      >
        <div
          style={{
            fontSize: 14,
            color: COLORS.cyan,
            fontFamily: FONT_FAMILY,
            textTransform: 'uppercase',
            letterSpacing: 2,
            marginBottom: 8,
          }}
        >
          How It Works
        </div>
        <div
          style={{
            fontSize: 40,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
          }}
        >
          Four steps to clarity
        </div>
      </div>

      {/* Progress dots */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          gap: 12,
          marginBottom: 30,
          opacity: sectionTitleOpacity,
        }}
      >
        {dots.map((dot, i) => (
          <div
            key={i}
            style={{
              width: dot.active ? 32 : 10,
              height: 10,
              borderRadius: 5,
              background: dot.active
                ? `linear-gradient(90deg, ${COLORS.purple}, ${COLORS.cyan})`
                : dot.done
                ? COLORS.purple
                : COLORS.cardBgLight,
              transition: 'none',
            }}
          />
        ))}
      </div>

      {/* Feature content */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 50,
          flex: 1,
          opacity: featureEnter * featureExit,
        }}
      >
        {/* Left: Text */}
        <div
          style={{
            flex: 1,
            transform: `translateX(${interpolate(featureEnter, [0, 1], [-40, 0])}px)`,
          }}
        >
          <div
            style={{
              fontSize: 13,
              color: COLORS.purple,
              fontFamily: FONT_FAMILY,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: 1,
              marginBottom: 12,
            }}
          >
            Step {activeIndex + 1}
          </div>
          <div
            style={{
              fontSize: 36,
              fontWeight: 700,
              fontFamily: FONT_FAMILY,
              color: COLORS.textPrimary,
              marginBottom: 16,
              lineHeight: 1.2,
            }}
          >
            {feature.title}
          </div>
          <div
            style={{
              fontSize: 18,
              color: COLORS.textSecondary,
              fontFamily: FONT_FAMILY,
              lineHeight: 1.6,
            }}
          >
            {feature.description}
          </div>
        </div>

        {/* Right: Screenshot */}
        <div
          style={{
            flex: 1.2,
            transform: `scale(${imageScale})`,
            borderRadius: 16,
            overflow: 'hidden',
            boxShadow: `0 20px 60px ${COLORS.purple}20, 0 0 0 1px ${COLORS.purple}30`,
          }}
        >
          <Img
            src={staticFile(feature.image)}
            style={{
              width: '100%',
              height: 'auto',
              display: 'block',
            }}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};
