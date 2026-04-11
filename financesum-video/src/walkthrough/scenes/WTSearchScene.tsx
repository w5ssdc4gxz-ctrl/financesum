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
 * Scene 2: Dashboard + Company Search
 * Shows dashboard with sidebar navigation and search interface.
 * Cursor clicks search bar, types "AAPL", dropdown appears with Apple Inc.
 * as top result, cursor moves to it and clicks.
 * Duration: 5s (150 frames at 30fps)
 *
 * Content area: 1840x960 (inside browser frame, below chrome bar)
 * Sidebar: 220px wide. Main content centered in remaining 1620px (padding 60).
 * Search bar center x: 220 + 60 + (1620-120)/2 = 1030.
 * Search bar center y: flex-centered vertically. Content block ~218px tall.
 *   Block starts at 60(pad) + (840-218)/2 = 371. Search bar at 371+166=537. Center y ≈ 563.
 * Apple dropdown result: y ≈ 563 + 26(half bar) + 6(gap) + 14(pad) + 18(half row) ≈ 627
 */
export const WTSearchScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // ── Transitions ──────────────────────────────────────────────────
  const fadeIn = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const fadeOut = interpolate(frame, [130, 150], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── 3D Camera ────────────────────────────────────────────────────
  const cameraProgress = interpolate(frame, [0, 150], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const camera = lerpCamera(
    CAMERA_PRESETS.search.from,
    CAMERA_PRESETS.search.to,
    cameraProgress,
  );

  // ── Typing animation "AAPL" ──────────────────────────────────────
  const typingText = 'AAPL';
  const typeStartFrame = 45;
  const charsTyped = Math.min(
    typingText.length,
    Math.max(0, Math.floor((frame - typeStartFrame) / 8)),
  );
  const displayText = typingText.substring(0, charsTyped);
  const isSearchFocused = frame > 32;
  const showCaret =
    frame > 35 && frame < 120 && Math.floor(frame / 15) % 2 === 0;

  // ── Dropdown spring (appears after 3+ chars: "AAP") ──────────────
  const dropdownProgress =
    charsTyped >= 3
      ? spring({
          frame: frame - (typeStartFrame + 24), // frame 69
          fps,
          config: { damping: 20, stiffness: 200 },
        })
      : 0;

  // ── Hover & click on Apple result ────────────────────────────────
  const isHovered = frame >= 100;
  const clickFrame = 110;

  // ── Cursor path (relative to content area 1840x960) ──────────────
  // Sidebar = 220px. Main content flex-centered in 1620px remaining (pad 60).
  // Search bar center: x≈1030, y≈563 (content block flex-centered vertically)
  // Apple result row: x≈1000, y≈627
  const cursorPath = [
    { x: 1300, y: 700, frame: 8 },     // start bottom-right
    { x: 1030, y: 563, frame: 30 },    // move to search bar center
    { x: 1030, y: 563, frame: 90 },    // stay during typing
    { x: 1000, y: 627, frame: 105 },   // move to Apple result
  ];

  // ── Sidebar nav items ────────────────────────────────────────────
  const navItems = [
    { icon: '\u25FB', label: 'Overview', active: true },
    { icon: '\u25CE', label: 'Coverage', active: false },
    { icon: '\u2261', label: 'Activity', active: false },
    { icon: '\u2605', label: 'Top Companies', active: false },
    { icon: '\u2699', label: 'Settings', active: false },
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: '#050505', opacity: fadeOut }}>
      <BrowserFrame url="financesums.com/dashboard" camera={camera}>
        <div
          style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            opacity: fadeIn,
            background: APP_COLORS.bgDark,
            position: 'relative',
          }}
        >
          {/* ── Sidebar ─────────────────────────────────────────── */}
          <div
            style={{
              width: 220,
              background: '#0d0d0d',
              borderRight: `1px solid ${APP_COLORS.border}`,
              display: 'flex',
              flexDirection: 'column',
              padding: '20px 0',
              flexShrink: 0,
            }}
          >
            {/* Logo */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '0 20px',
                marginBottom: 32,
              }}
            >
              <Img
                src={staticFile('logo.png')}
                style={{ width: 24, height: 24, borderRadius: 5 }}
              />
              <span
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  color: APP_COLORS.textWhite,
                  fontFamily: FONT_FAMILY,
                }}
              >
                FinanceSum
              </span>
            </div>

            {/* Nav items */}
            {navItems.map((item, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 20px',
                  fontSize: 14,
                  fontFamily: FONT_FAMILY,
                  background: item.active
                    ? 'rgba(255,255,255,0.05)'
                    : 'transparent',
                  borderLeft: item.active
                    ? '2px solid #fff'
                    : '2px solid transparent',
                  color: item.active
                    ? APP_COLORS.textWhite
                    : APP_COLORS.textMuted,
                  fontWeight: item.active ? 600 : 400,
                }}
              >
                <span
                  style={{
                    fontSize: 14,
                    color: APP_COLORS.textMuted,
                    lineHeight: 1,
                  }}
                >
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </div>
            ))}
          </div>

          {/* ── Main content ────────────────────────────────────── */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 60,
            }}
          >
            <div
              style={{
                maxWidth: 600,
                width: '100%',
                textAlign: 'center',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
              }}
            >
              {/* File icon */}
              <div
                style={{
                  fontSize: 48,
                  marginBottom: 16,
                  opacity: 0.4,
                }}
              >
                {'\uD83D\uDCC4'}
              </div>

              {/* Heading */}
              <div
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  color: APP_COLORS.textWhite,
                  fontFamily: FONT_FAMILY,
                  marginBottom: 12,
                }}
              >
                Analyze New Company
              </div>

              {/* Subheading */}
              <div
                style={{
                  fontSize: 15,
                  color: APP_COLORS.textMuted,
                  fontFamily: FONT_FAMILY,
                  marginBottom: 28,
                }}
              >
                Search for any publicly traded company to get started
              </div>

              {/* ── Search bar container (relative for dropdown) ── */}
              <div style={{ position: 'relative', width: '100%' }}>
                {/* Search bar */}
                <div
                  style={{
                    width: '100%',
                    height: 52,
                    background: APP_COLORS.bgCard,
                    border: isSearchFocused
                      ? `2px solid ${APP_COLORS.primaryBlue}`
                      : `1px solid ${APP_COLORS.border}`,
                    borderRadius: 12,
                    display: 'flex',
                    alignItems: 'center',
                    padding: '0 20px',
                    gap: 12,
                    boxShadow: isSearchFocused
                      ? '0 0 20px rgba(0,21,255,0.15)'
                      : 'none',
                  }}
                >
                  {/* Search magnifying glass icon */}
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 18 18"
                    fill="none"
                  >
                    <circle
                      cx="7.5"
                      cy="7.5"
                      r="6"
                      stroke="#666"
                      strokeWidth="1.5"
                    />
                    <line
                      x1="12"
                      y1="12"
                      x2="16"
                      y2="16"
                      stroke="#666"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                    />
                  </svg>

                  {/* Text / placeholder */}
                  <span
                    style={{
                      fontSize: 16,
                      color:
                        charsTyped > 0
                          ? APP_COLORS.textWhite
                          : APP_COLORS.textMuted,
                      fontFamily: FONT_FAMILY,
                      flex: 1,
                      textAlign: 'left',
                    }}
                  >
                    {charsTyped > 0
                      ? displayText
                      : 'Search by ticker or company name...'}
                    {showCaret && (
                      <span style={{ color: APP_COLORS.primaryBlue }}>|</span>
                    )}
                  </span>
                </div>

                {/* ── Dropdown results ───────────────────────────── */}
                {dropdownProgress > 0 && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 58,
                      left: 0,
                      right: 0,
                      background: APP_COLORS.bgCard,
                      border: `1px solid ${APP_COLORS.border}`,
                      borderRadius: 12,
                      overflow: 'hidden',
                      opacity: dropdownProgress,
                      transform: `translateY(${interpolate(dropdownProgress, [0, 1], [8, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}px)`,
                      boxShadow: '0 8px 30px rgba(0,0,0,0.3)',
                    }}
                  >
                    {/* Apple Inc. result */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 14,
                        padding: '14px 20px',
                        background: isHovered
                          ? 'rgba(255,255,255,0.05)'
                          : 'transparent',
                      }}
                    >
                      {/* Apple logo placeholder */}
                      <div
                        style={{
                          width: 36,
                          height: 36,
                          borderRadius: 8,
                          background: '#1a1a1a',
                          border: '1px solid #333',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 18,
                          color: APP_COLORS.textGray,
                          flexShrink: 0,
                        }}
                      >
                        {/* Apple icon SVG */}
                        <svg
                          width="16"
                          height="18"
                          viewBox="0 0 16 18"
                          fill="none"
                        >
                          <path
                            d="M12.5 14.5C11.8 15.5 11 16.5 9.8 16.5C8.6 16.5 8.2 15.8 6.8 15.8C5.4 15.8 4.9 16.5 3.8 16.5C2.6 16.5 1.8 15.4 1.1 14.4C-0.1 12.6 -0.3 10.4 0.6 9.2C1.2 8.3 2.2 7.7 3.2 7.7C4.4 7.7 5.1 8.4 6.2 8.4C7.3 8.4 7.8 7.7 9.2 7.7C10.1 7.7 11 8.1 11.6 8.9C9.7 10 10 12.7 12.5 14.5ZM9.5 6.5C10 5.8 10.4 4.8 10.3 3.8C9.4 3.9 8.4 4.5 7.8 5.2C7.3 5.9 6.8 6.9 7 7.8C7.9 7.8 8.9 7.2 9.5 6.5Z"
                            fill="#999"
                          />
                        </svg>
                      </div>

                      <div style={{ flex: 1 }}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                          }}
                        >
                          <span
                            style={{
                              fontSize: 15,
                              fontWeight: 700,
                              color: APP_COLORS.textWhite,
                              fontFamily: FONT_FAMILY,
                            }}
                          >
                            Apple Inc.
                          </span>
                          <span
                            style={{
                              fontSize: 12,
                              fontWeight: 600,
                              color: APP_COLORS.primaryBlue,
                              fontFamily: FONT_FAMILY,
                              background: 'rgba(0,21,255,0.1)',
                              padding: '2px 8px',
                              borderRadius: 4,
                            }}
                          >
                            AAPL
                          </span>
                        </div>
                        <span
                          style={{
                            fontSize: 12,
                            color: APP_COLORS.textMuted,
                            fontFamily: FONT_FAMILY,
                          }}
                        >
                          NASDAQ · Technology
                        </span>
                      </div>
                    </div>

                    {/* Other results (dimmed) */}
                    {[
                      {
                        name: 'Aaon Inc.',
                        ticker: 'AAON',
                        exchange: 'NASDAQ',
                      },
                      {
                        name: 'AAR Corp.',
                        ticker: 'AIR',
                        exchange: 'NYSE',
                      },
                    ].map((item, i) => (
                      <div
                        key={i}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 14,
                          padding: '12px 20px',
                          opacity: 0.5,
                          borderTop: `1px solid ${APP_COLORS.border}`,
                        }}
                      >
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 8,
                            background: '#1a1a1a',
                            border: '1px solid #333',
                            flexShrink: 0,
                          }}
                        />
                        <div style={{ flex: 1 }}>
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                            }}
                          >
                            <span
                              style={{
                                fontSize: 14,
                                fontWeight: 600,
                                color: APP_COLORS.textGray,
                                fontFamily: FONT_FAMILY,
                              }}
                            >
                              {item.name}
                            </span>
                            <span
                              style={{
                                fontSize: 11,
                                fontWeight: 600,
                                color: APP_COLORS.textMuted,
                                fontFamily: FONT_FAMILY,
                              }}
                            >
                              {item.ticker}
                            </span>
                          </div>
                          <span
                            style={{
                              fontSize: 12,
                              color: APP_COLORS.textMuted,
                              fontFamily: FONT_FAMILY,
                            }}
                          >
                            {item.exchange}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Cursor INSIDE BrowserFrame — transforms with 3D perspective */}
          <Cursor
            path={cursorPath}
            clickFrames={[35, clickFrame]}
            visible={frame > 8 && frame < 140}
          />
        </div>
      </BrowserFrame>
    </AbsoluteFill>
  );
};
