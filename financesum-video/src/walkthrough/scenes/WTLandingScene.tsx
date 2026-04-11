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
import { BrowserFrame } from '../BrowserFrame';
import { Cursor } from '../Cursor';
import { APP_COLORS, FPS, FONT_FAMILY, CAMERA_PRESETS, lerpCamera } from '../constants';

/**
 * Scene 1: Landing page hero
 * Shows the FinanceSum landing page with hero section inside a 3D-perspective browser frame.
 * Cursor appears and moves to "Start Analyzing" button, clicks it.
 * Duration: 4s (120 frames at 30fps)
 *
 * Content area: 1840x960 (inside browser frame, below chrome bar)
 * Cursor is rendered INSIDE BrowserFrame so it transforms with the 3D perspective.
 */
export const WTLandingScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // 3D camera animation across the full scene
  const cameraProgress = interpolate(frame, [0, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Page fade in (frames 0-15)
  const pageOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Fade out after click (frames 100-120)
  const fadeOut = interpolate(frame, [100, 120], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Button target position within content area (1840x960)
  // Hero section: flex:1 (896px), justifyContent center, marginTop -40.
  // Content block: badge(53) + h1(87) + h2(103) + subtitle(98) + CTA(45) + trusted(76) = 462px.
  // Block top: 64 + (896-462)/2 - 40 = 241. CTA top: 241+53+87+103+98 = 582. CTA center y = 604.
  // "Start Analyzing" btn: ~202px wide. Row(406px) centered in 1840. Btn center x = 717+101 = 818.
  const btnX = 818;
  const btnY = 604;

  // Cursor path: coordinates relative to content area (1840x960)
  // Start at bottom-right area, move to the "Start Analyzing" button center
  const cursorPath = [
    { x: 1400, y: 750, frame: 25 },
    { x: btnX, y: btnY, frame: 75 },
  ];

  // Click at frame 85
  const clickFrame = 85;

  // After click: button glow and scale effect
  const postClick = frame > clickFrame;
  const btnGlow = postClick
    ? interpolate(frame, [clickFrame, clickFrame + 15], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 0;

  // Button press spring for satisfying click feel
  const btnPress = postClick
    ? spring({
        frame: frame - clickFrame,
        fps,
        config: { damping: 12, stiffness: 300, mass: 0.5 },
      })
    : 0;

  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: fadeOut }}>
      <BrowserFrame
        url="financesums.com"
        camera={lerpCamera(
          CAMERA_PRESETS.landing.from,
          CAMERA_PRESETS.landing.to,
          cameraProgress,
        )}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            background: `radial-gradient(ellipse at 50% 30%, #0a0a2e 0%, ${APP_COLORS.bgDark} 70%)`,
            display: 'flex',
            flexDirection: 'column',
            opacity: pageOpacity,
            position: 'relative',
          }}
        >
          {/* Navbar */}
          <div
            style={{
              height: 64,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0 40px',
              borderBottom: `1px solid ${APP_COLORS.border}`,
              flexShrink: 0,
            }}
          >
            {/* Left: logo + name */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Img
                src={staticFile('logo.png')}
                style={{ width: 28, height: 28, borderRadius: 6 }}
              />
              <span
                style={{
                  fontSize: 18,
                  fontWeight: 700,
                  color: APP_COLORS.textWhite,
                  fontFamily: FONT_FAMILY,
                }}
              >
                FinanceSum
              </span>
            </div>

            {/* Right: Sign In + Get Started */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
              <span
                style={{
                  fontSize: 14,
                  color: APP_COLORS.textGray,
                  fontFamily: FONT_FAMILY,
                }}
              >
                Sign In
              </span>
              <div
                style={{
                  background: APP_COLORS.primaryBlue,
                  color: '#fff',
                  fontSize: 14,
                  fontWeight: 600,
                  fontFamily: FONT_FAMILY,
                  padding: '8px 20px',
                  borderRadius: 20,
                }}
              >
                Get Started
              </div>
            </div>
          </div>

          {/* Hero section */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '0 80px',
              marginTop: -40,
            }}
          >
            {/* Beta badge */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                background: 'rgba(255,255,255,0.06)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 20,
                padding: '6px 16px',
                marginBottom: 28,
              }}
            >
              <div
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: APP_COLORS.primaryBlue,
                }}
              />
              <span
                style={{
                  fontSize: 13,
                  color: APP_COLORS.textGray,
                  fontFamily: FONT_FAMILY,
                }}
              >
                Now in Public Beta
              </span>
            </div>

            {/* Headline line 1 */}
            <div
              style={{
                fontSize: 72,
                fontWeight: 800,
                color: APP_COLORS.textWhite,
                fontFamily: FONT_FAMILY,
                textAlign: 'center',
                lineHeight: 1.1,
                marginBottom: 8,
              }}
            >
              Financial analysis,
            </div>

            {/* Headline line 2 */}
            <div
              style={{
                fontSize: 72,
                fontWeight: 800,
                color: APP_COLORS.primaryBlue,
                fontFamily: FONT_FAMILY,
                textAlign: 'center',
                lineHeight: 1.1,
                marginBottom: 24,
                fontStyle: 'italic',
              }}
            >
              reimagined.
            </div>

            {/* Subtitle */}
            <div
              style={{
                fontSize: 18,
                color: APP_COLORS.textGray,
                fontFamily: FONT_FAMILY,
                textAlign: 'center',
                maxWidth: 600,
                lineHeight: 1.6,
                marginBottom: 40,
              }}
            >
              Digest 10-K and 10-Q filings into executive-grade investment memos
              powered by AI, with 10 legendary investor personas.
            </div>

            {/* CTA buttons row */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              {/* Start Analyzing button */}
              <div
                style={{
                  background: postClick
                    ? `rgba(0, 21, 255, ${0.8 + btnGlow * 0.2})`
                    : APP_COLORS.primaryBlue,
                  color: '#fff',
                  fontSize: 16,
                  fontWeight: 700,
                  fontFamily: FONT_FAMILY,
                  padding: '14px 36px',
                  borderRadius: 28,
                  boxShadow: postClick
                    ? `0 0 ${30 * btnGlow}px rgba(0,21,255,0.6)`
                    : 'none',
                  transform: postClick
                    ? `scale(${1 - btnPress * 0.03})`
                    : 'scale(1)',
                }}
              >
                Start Analyzing
              </div>

              {/* See How It Works button */}
              <div
                style={{
                  color: APP_COLORS.textGray,
                  fontSize: 16,
                  fontWeight: 500,
                  fontFamily: FONT_FAMILY,
                  padding: '14px 24px',
                  borderRadius: 28,
                  border: `1px solid ${APP_COLORS.border}`,
                }}
              >
                See How It Works
              </div>
            </div>

            {/* Trusted by row */}
            <div
              style={{
                marginTop: 60,
                display: 'flex',
                alignItems: 'center',
                gap: 32,
                opacity: 0.3,
              }}
            >
              {['BlackRock', 'JP Morgan', 'Goldman Sachs', 'Citadel', 'Bridgewater'].map(
                (name, i) => (
                  <span
                    key={i}
                    style={{
                      fontSize: 13,
                      color: APP_COLORS.textGray,
                      fontFamily: FONT_FAMILY,
                      fontWeight: 600,
                      letterSpacing: 1.5,
                      textTransform: 'uppercase' as const,
                    }}
                  >
                    {name}
                  </span>
                ),
              )}
            </div>
          </div>

          {/* Cursor INSIDE BrowserFrame — transforms with 3D perspective */}
          <Cursor
            path={cursorPath}
            clickFrames={[clickFrame]}
            visible={frame > 25 && frame < 115}
          />
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
