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
 * Scene 3: Company page for Apple (AAPL)
 * Shows the company page with neo-brutalist header, filing dropdown, and "Custom" button.
 * Cursor moves to "Custom" button and clicks.
 * Duration: 4s (120 frames at 30fps)
 *
 * Content area: 1840x960 (inside browser frame, below chrome bar)
 * Layout: navbar(56px), content padding(32px top, 48px sides).
 * Left column "Generate Summary" card starts at x=48, width=340.
 * "Custom" button: inside left column card, centered horizontally.
 *   x ≈ 48 + 170 = 218, y ≈ 56(nav) + 32(pad) + ~140(header card) + 32(gap) + 24(card pad) + 16(title mb) + 42(selector) + 16(mb) + 20(btn half) ≈ 400
 */
export const WTCompanyScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // 3D camera animation across the full scene
  const cameraProgress = interpolate(frame, [0, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Page fade in (frames 0-12)
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Fade out (frames 100-120)
  const fadeOut = interpolate(frame, [100, 120], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Custom button position (relative to content area 1840x960)
  // box-sizing: content-box (no CSS reset in project)
  // y: nav(56 content + 2 border = 58) + pad(32) + header card(~138) + mb(32) +
  //    tabs(~42) + mb(28) + card border(2) + card pad(24) + title(~20) + mb(16) +
  //    selector(42) + mb(16) + half btn(23) = ~473
  // x: content pad(48) + card border(2) + card pad(24) + half card content(170) = 244
  const customBtnX = 244;
  const customBtnY = 473;

  // Cursor path: coordinates relative to content area (1840x960)
  const cursorPath = [
    { x: 800, y: 300, frame: 8 },
    { x: 500, y: 380, frame: 40 },
    { x: customBtnX, y: customBtnY, frame: 70 },
  ];

  // Click at frame 80
  const clickFrame = 80;
  const postClick = frame > clickFrame;

  // Button glow after click
  const btnGlow = postClick
    ? interpolate(frame, [clickFrame, clickFrame + 10], [0, 1], {
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
        url="financesums.com/company/apple-inc"
        camera={lerpCamera(
          CAMERA_PRESETS.companyPage.from,
          CAMERA_PRESETS.companyPage.to,
          cameraProgress,
        )}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            background: '#fafafa',
            display: 'flex',
            flexDirection: 'column',
            opacity: fadeIn,
            position: 'relative',
          }}
        >
          {/* Navbar */}
          <div
            style={{
              height: 56,
              background: '#ffffff',
              borderBottom: '2px solid #000',
              display: 'flex',
              alignItems: 'center',
              padding: '0 32px',
              gap: 10,
              flexShrink: 0,
            }}
          >
            <Img
              src={staticFile('logo.png')}
              style={{ width: 24, height: 24, borderRadius: 5 }}
            />
            <span
              style={{
                fontSize: 16,
                fontWeight: 800,
                color: '#000',
                fontFamily: FONT_FAMILY,
                textTransform: 'uppercase',
                letterSpacing: 1,
              }}
            >
              FINANCESUM
            </span>
          </div>

          {/* Content */}
          <div style={{ flex: 1, padding: '32px 48px', overflow: 'hidden' }}>
            {/* Company header card - neo-brutalist */}
            <div
              style={{
                background: '#ffffff',
                border: '2px solid #000',
                boxShadow: APP_COLORS.brutalShadow,
                borderRadius: 0,
                padding: '28px 36px',
                display: 'flex',
                alignItems: 'center',
                gap: 24,
                marginBottom: 32,
              }}
            >
              {/* Company logo placeholder */}
              <div
                style={{
                  width: 72,
                  height: 72,
                  background: '#f0f0f0',
                  border: '2px solid #000',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 36,
                  fontWeight: 800,
                  fontFamily: FONT_FAMILY,
                  flexShrink: 0,
                }}
              >
                {'\uF8FF'}
              </div>

              {/* Middle: Company name + badges */}
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 32,
                    fontWeight: 900,
                    color: '#000',
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 1,
                  }}
                >
                  APPLE INC.
                </div>
                <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      fontFamily: FONT_FAMILY,
                      background: '#dbeafe',
                      color: '#1d4ed8',
                      padding: '4px 12px',
                      border: '2px solid #000',
                    }}
                  >
                    AAPL
                  </span>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      fontFamily: FONT_FAMILY,
                      background: '#fff',
                      color: '#000',
                      padding: '4px 12px',
                      border: '2px solid #000',
                    }}
                  >
                    NASDAQ
                  </span>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      fontFamily: FONT_FAMILY,
                      background: '#fef9c3',
                      color: '#854d0e',
                      padding: '4px 12px',
                      border: '2px solid #000',
                    }}
                  >
                    Technology
                  </span>
                </div>
              </div>

              {/* Right: Health Score */}
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  border: '2px solid #000',
                  padding: '12px 24px',
                  background: '#f0fdf4',
                  flexShrink: 0,
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 1,
                    color: '#666',
                  }}
                >
                  HEALTH SCORE
                </span>
                <span
                  style={{
                    fontSize: 36,
                    fontWeight: 900,
                    fontFamily: FONT_FAMILY,
                    color: APP_COLORS.healthGreen,
                  }}
                >
                  87
                </span>
              </div>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 28 }}>
              <div
                style={{
                  padding: '10px 28px',
                  background: '#000',
                  color: '#fff',
                  fontSize: 13,
                  fontWeight: 800,
                  fontFamily: FONT_FAMILY,
                  textTransform: 'uppercase',
                  letterSpacing: 1.5,
                  border: '2px solid #000',
                }}
              >
                Overview
              </div>
              <div
                style={{
                  padding: '10px 28px',
                  background: '#fff',
                  color: '#000',
                  fontSize: 13,
                  fontWeight: 800,
                  fontFamily: FONT_FAMILY,
                  textTransform: 'uppercase',
                  letterSpacing: 1.5,
                  border: '2px solid #000',
                  borderLeft: 'none',
                }}
              >
                Filings
              </div>
            </div>

            {/* Two-column layout */}
            <div style={{ display: 'flex', gap: 28 }}>
              {/* Left column - Generate Summary card */}
              <div
                style={{
                  width: 340,
                  background: '#fff',
                  border: '2px solid #000',
                  boxShadow: APP_COLORS.brutalShadow,
                  padding: 24,
                  flexShrink: 0,
                }}
              >
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 800,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 1,
                    marginBottom: 16,
                    color: '#000',
                  }}
                >
                  GENERATE SUMMARY
                </div>

                {/* Filing selector */}
                <div
                  style={{
                    border: '2px solid #000',
                    padding: '10px 16px',
                    marginBottom: 16,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span
                    style={{
                      fontSize: 14,
                      fontFamily: FONT_FAMILY,
                      fontWeight: 600,
                      color: '#000',
                    }}
                  >
                    10-K {'\u00B7'} 2024-11-01
                  </span>
                  <span style={{ fontSize: 12, color: '#666' }}>{'\u25BC'}</span>
                </div>

                {/* Custom button */}
                <div
                  style={{
                    background: postClick ? '#1a1a1a' : '#000',
                    color: '#fff',
                    padding: '12px 0',
                    textAlign: 'center' as const,
                    fontSize: 14,
                    fontWeight: 800,
                    fontFamily: FONT_FAMILY,
                    textTransform: 'uppercase',
                    letterSpacing: 2,
                    border: '2px solid #000',
                    boxShadow: postClick
                      ? 'none'
                      : '3px 3px 0px 0px rgba(0,0,0,0.3)',
                    transform: postClick
                      ? 'translate(2px, 2px)'
                      : 'translate(0, 0)',
                  }}
                >
                  Custom
                </div>
              </div>

              {/* Right column - No summaries placeholder */}
              <div
                style={{
                  flex: 1,
                  background: '#fff',
                  border: '2px solid #000',
                  boxShadow: APP_COLORS.brutalShadow,
                  padding: '48px 36px',
                  textAlign: 'center' as const,
                }}
              >
                <div
                  style={{
                    fontSize: 32,
                    marginBottom: 12,
                    opacity: 0.3,
                  }}
                >
                  {'\uD83D\uDCCB'}
                </div>
                <div
                  style={{
                    fontSize: 16,
                    fontWeight: 700,
                    fontFamily: FONT_FAMILY,
                    color: '#333',
                    marginBottom: 8,
                  }}
                >
                  No summaries yet
                </div>
                <div
                  style={{
                    fontSize: 14,
                    fontFamily: FONT_FAMILY,
                    color: '#999',
                  }}
                >
                  Generate your first AI financial brief for Apple
                </div>
              </div>
            </div>
          </div>

          {/* Cursor INSIDE BrowserFrame — transforms with 3D perspective */}
          <Cursor
            path={cursorPath}
            clickFrames={[clickFrame]}
            visible={frame > 8 && frame < 110}
          />
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
