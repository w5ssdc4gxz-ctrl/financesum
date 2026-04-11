import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { COLORS, FONT_FAMILY } from '../constants';

const PLANS = [
  {
    name: 'Free',
    price: '$0',
    period: '/month',
    features: ['1 summary trial', 'Basic analysis', 'Standard exports'],
    highlight: false,
    color: COLORS.textSecondary,
  },
  {
    name: 'Pro',
    price: '$20',
    period: '/month',
    badge: 'Most Popular',
    features: [
      '100 summaries/mo',
      'Health scores',
      'Investor personas',
      'All export formats',
      'Priority support',
    ],
    highlight: true,
    color: COLORS.purple,
  },
  {
    name: 'Enterprise',
    price: 'Custom',
    period: '',
    features: ['Unlimited summaries', 'API access', 'Team collaboration', 'Custom integrations'],
    highlight: false,
    color: COLORS.cyan,
  },
];

export const PricingScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleProgress = spring({
    frame,
    fps,
    config: { damping: 18 },
  });

  // Trust badges
  const badgesOpacity = interpolate(frame, [3 * fps, 3.8 * fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.darkerBg,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '80px 100px',
      }}
    >
      {/* Title */}
      <div
        style={{
          opacity: titleProgress,
          transform: `translateY(${interpolate(titleProgress, [0, 1], [30, 0])}px)`,
          textAlign: 'center',
          marginBottom: 60,
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
          Simple Pricing
        </div>
        <div
          style={{
            fontSize: 48,
            fontWeight: 800,
            fontFamily: FONT_FAMILY,
            color: COLORS.textPrimary,
          }}
        >
          Choose your plan
        </div>
      </div>

      {/* Cards row */}
      <div style={{ display: 'flex', gap: 36, alignItems: 'stretch' }}>
        {PLANS.map((plan, i) => {
          const cardProgress = spring({
            frame: frame - Math.round((0.5 + i * 0.3) * fps),
            fps,
            config: { damping: 14 },
          });

          return (
            <div
              key={i}
              style={{
                transform: `scale(${cardProgress}) translateY(${interpolate(cardProgress, [0, 1], [30, 0])}px)`,
                opacity: cardProgress,
                background: plan.highlight
                  ? `linear-gradient(180deg, ${COLORS.purple}15, ${COLORS.cardBg})`
                  : COLORS.cardBg,
                borderRadius: 24,
                padding: '40px 40px',
                width: 320,
                border: plan.highlight
                  ? `2px solid ${COLORS.purple}60`
                  : `1px solid ${COLORS.textMuted}20`,
                display: 'flex',
                flexDirection: 'column' as const,
                position: 'relative' as const,
              }}
            >
              {/* Badge */}
              {plan.badge && (
                <div
                  style={{
                    position: 'absolute',
                    top: -14,
                    left: '50%',
                    transform: 'translateX(-50%)',
                    background: `linear-gradient(90deg, ${COLORS.purple}, ${COLORS.cyan})`,
                    color: '#fff',
                    fontSize: 11,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    padding: '6px 20px',
                    borderRadius: 20,
                    textTransform: 'uppercase',
                    letterSpacing: 1.5,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {plan.badge}
                </div>
              )}

              {/* Plan name */}
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: plan.color,
                  fontFamily: FONT_FAMILY,
                  marginBottom: 16,
                }}
              >
                {plan.name}
              </div>

              {/* Price */}
              <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 28 }}>
                <span
                  style={{
                    fontSize: 52,
                    fontWeight: 800,
                    color: COLORS.textPrimary,
                    fontFamily: FONT_FAMILY,
                  }}
                >
                  {plan.price}
                </span>
                <span
                  style={{
                    fontSize: 16,
                    color: COLORS.textMuted,
                    fontFamily: FONT_FAMILY,
                    marginLeft: 4,
                  }}
                >
                  {plan.period}
                </span>
              </div>

              {/* Features */}
              {plan.features.map((feat, j) => {
                const featOpacity = interpolate(
                  frame,
                  [(1.2 + i * 0.3 + j * 0.15) * fps, (1.5 + i * 0.3 + j * 0.15) * fps],
                  [0, 1],
                  { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
                );
                return (
                  <div
                    key={j}
                    style={{
                      opacity: featOpacity,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      fontSize: 15,
                      color: COLORS.textSecondary,
                      fontFamily: FONT_FAMILY,
                      lineHeight: 2.4,
                    }}
                  >
                    <span style={{ color: COLORS.green, fontWeight: 700 }}>{'\u2713'}</span>
                    {feat}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Trust badges */}
      <div
        style={{
          position: 'absolute',
          bottom: 60,
          left: 0,
          right: 0,
          display: 'flex',
          justifyContent: 'center',
          gap: 36,
          opacity: badgesOpacity,
        }}
      >
        {['SSL Secured', 'No Credit Card Required', 'Cancel Anytime'].map((badge, i) => (
          <div
            key={i}
            style={{
              fontSize: 13,
              color: COLORS.textMuted,
              fontFamily: FONT_FAMILY,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span style={{ color: COLORS.green }}>{'\u2713'}</span>
            {badge}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
