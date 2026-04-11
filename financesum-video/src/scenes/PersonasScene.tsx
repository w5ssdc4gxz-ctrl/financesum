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
import { COLORS, INVESTORS, FONT_FAMILY } from '../constants';

export const PersonasScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Title animation
  const titleOpacity = interpolate(frame, [0, 0.5 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Carousel: show 5 at a time, scroll through
  const scrollOffset = interpolate(frame, [1 * fps, 5 * fps], [0, -700], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(135deg, ${COLORS.darkerBg} 0%, #1a0a30 50%, ${COLORS.darkerBg} 100%)`,
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 60,
      }}
    >
      {/* Title */}
      <div style={{ opacity: titleOpacity, textAlign: 'center', marginBottom: 16 }}>
        <div
          style={{
            fontSize: 14,
            color: COLORS.purple,
            fontFamily: FONT_FAMILY,
            textTransform: 'uppercase',
            letterSpacing: 2,
            marginBottom: 8,
          }}
        >
          Investor Personas
        </div>
        <div
          style={{
            fontSize: 40,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
            marginBottom: 8,
          }}
        >
          See through the eyes of legends
        </div>
        <div
          style={{
            fontSize: 18,
            color: COLORS.textSecondary,
            fontFamily: FONT_FAMILY,
          }}
        >
          Every summary written in the voice of 10 legendary investors
        </div>
      </div>

      {/* Investor cards carousel */}
      <div
        style={{
          width: '100%',
          overflow: 'hidden',
          marginTop: 40,
        }}
      >
        <div
          style={{
            display: 'flex',
            gap: 20,
            transform: `translateX(${scrollOffset}px)`,
            paddingLeft: 80,
          }}
        >
          {INVESTORS.map((investor, i) => {
            const cardScale = spring({
              frame: frame - Math.round((0.5 + i * 0.15) * fps),
              fps,
              config: { damping: 15 },
            });

            // Highlight effect for currently centered card
            const centerX = -scrollOffset + 540; // approximate center
            const cardX = i * 200 + 80;
            const distFromCenter = Math.abs(centerX - cardX);
            const highlightOpacity = interpolate(distFromCenter, [0, 300], [1, 0.5], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });

            return (
              <div
                key={i}
                style={{
                  transform: `scale(${cardScale})`,
                  opacity: highlightOpacity,
                  minWidth: 180,
                  background: COLORS.cardBg,
                  borderRadius: 20,
                  padding: 20,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  border: `1px solid ${COLORS.purple}30`,
                  boxShadow: `0 8px 32px ${COLORS.purple}15`,
                }}
              >
                {/* Investor photo */}
                <div
                  style={{
                    width: 90,
                    height: 90,
                    borderRadius: '50%',
                    overflow: 'hidden',
                    marginBottom: 14,
                    border: `2px solid ${COLORS.purple}60`,
                  }}
                >
                  <Img
                    src={staticFile(investor.image)}
                    style={{
                      width: '100%',
                      height: '100%',
                      objectFit: 'cover',
                    }}
                  />
                </div>
                {/* Name */}
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    color: COLORS.textPrimary,
                    fontFamily: FONT_FAMILY,
                    textAlign: 'center',
                    marginBottom: 4,
                  }}
                >
                  {investor.name}
                </div>
                {/* Style */}
                <div
                  style={{
                    fontSize: 11,
                    color: COLORS.cyan,
                    fontFamily: FONT_FAMILY,
                    textAlign: 'center',
                    background: `${COLORS.cyan}15`,
                    padding: '4px 10px',
                    borderRadius: 20,
                  }}
                >
                  {investor.style}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
